"""Build final locale files for WHM import."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CP_NS = "tag:cpanel.net,2012-01:translate"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_child(unit: ET.Element, child_name: str) -> ET.Element | None:
    for child in unit:
        if local_name(child.tag) == child_name:
            return child
    return None


def element_xml(element: ET.Element | None) -> str:
    return ET.tostring(element, encoding="unicode") if element is not None else ""


def element_fingerprint(element: ET.Element | None) -> object:
    if element is None:
        return None
    return {
        "tag": element.tag,
        "attrs": sorted(element.attrib.items()),
        "text": element.text or "",
        "tail": element.tail or "",
        "children": [element_fingerprint(child) for child in list(element)],
    }


def source_hash(source: ET.Element | None) -> str:
    payload = json.dumps(element_fingerprint(source), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_body(root: ET.Element) -> ET.Element:
    for element in root.iter():
        if local_name(element.tag) == "body":
            return element
    raise ValueError("XLF file does not contain a body element")


def get_file(root: ET.Element) -> ET.Element:
    for element in root.iter():
        if local_name(element.tag) == "file":
            return element
    raise ValueError("XLF file does not contain a file element")


PLURAL_CTYPES = {"x-implied", "x-explicit"}


def has_plural_helper(element: ET.Element) -> bool:
    for child in element.iter():
        if child.attrib.get("ctype") in PLURAL_CTYPES:
            return True
    return False


def is_empty_source_unit(element: ET.Element) -> bool:
    if local_name(element.tag) != "trans-unit":
        return False
    source = find_child(element, "source")
    if source is None:
        return True
    return not "".join(source.itertext()).strip() and not list(source)


def has_empty_source_unit(element: ET.Element) -> bool:
    return any(is_empty_source_unit(child) for child in element.iter())


def remove_plural_units(root: ET.Element) -> int:
    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            child_name = local_name(child.tag)
            if child_name == "group" and (has_plural_helper(child) or has_empty_source_unit(child)):
                parent.remove(child)
                removed += 1
            elif child_name == "trans-unit" and (has_plural_helper(child) or is_empty_source_unit(child)):
                parent.remove(child)
                removed += 1
    return removed


def replace_or_insert_target(unit: ET.Element, new_target: ET.Element) -> None:
    old_target = find_child(unit, "target")
    if old_target is not None:
        children = list(unit)
        index = children.index(old_target)
        unit.remove(old_target)
        unit.insert(index, new_target)
        return

    source = find_child(unit, "source")
    if source is not None:
        unit.insert(list(unit).index(source) + 1, new_target)
    else:
        unit.append(new_target)


def target_from_source(source: ET.Element | None) -> ET.Element:
    target = ET.Element(f"{{{XLIFF_NS}}}target")
    target.set("state", "needs-translation")
    if source is None:
        return target

    target.text = source.text
    for child in list(source):
        target.append(deepcopy(child))
    return target


def validate_xml(path: Path) -> None:
    ET.parse(path)


def build_locale(input_path: Path, output_path: Path) -> int:
    validate_xml(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, output_path)
    print(f"Built locale: {output_path}")
    return 0


def ensure_db_tables(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'locale_units'"
    ).fetchone()
    if row is None:
        raise RuntimeError("locale DB tables were not found. Run scripts/import_locale_to_db.py first.")


def best_target_xml(conn: sqlite3.Connection, unit_id: str, hash_value: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        """
        SELECT target_xml, origin
        FROM locale_targets
        WHERE unit_id = ?
          AND source_hash = ?
          AND quality_status IN ('valid', 'approved')
        ORDER BY
          CASE
            WHEN origin = 'manual' AND is_reviewed = 1 THEN 1
            WHEN origin = 'ai_cache' AND quality_status = 'approved' THEN 2
            WHEN origin = 'ai_cache' THEN 3
            WHEN origin = 'cpanel' THEN 4
            ELSE 5
          END,
          updated_at DESC,
          target_id DESC
        LIMIT 1
        """,
        (unit_id, hash_value),
    ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def apply_db_targets(tree: ET.ElementTree, conn: sqlite3.Connection) -> dict[str, int]:
    stats = {"translated": 0, "pending": 0}
    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        unit_id = unit.attrib.get("id")
        if not unit_id:
            continue
        source = find_child(unit, "source")
        hash_value = source_hash(source)
        target_xml, origin = best_target_xml(conn, unit_id, hash_value)
        if target_xml:
            target = ET.fromstring(target_xml)
            target.set("state", "translated")
            if origin:
                target.set(f"{{{CP_NS}}}origin", origin)
            stats["translated"] += 1
        else:
            target = target_from_source(source)
            stats["pending"] += 1
        replace_or_insert_target(unit, target)
    return stats


def append_extended_units(tree: ET.ElementTree, conn: sqlite3.Connection) -> int:
    body = get_body(tree.getroot())
    existing_ids = {
        unit.attrib.get("id")
        for unit in tree.getroot().iter()
        if local_name(unit.tag) == "trans-unit" and unit.attrib.get("id")
    }
    added = 0

    rows = conn.execute(
        """
        SELECT u.unit_id, u.source_hash, u.unit_xml
        FROM locale_units
        AS u
        WHERE u.extended = 1 AND u.canonical = 0
        ORDER BY
          u.unit_id,
          CASE
            WHEN EXISTS (
              SELECT 1
              FROM locale_targets t
              WHERE t.unit_id = u.unit_id
                AND t.source_hash = u.source_hash
                AND t.quality_status IN ('valid', 'approved')
            ) THEN 0
            ELSE 1
          END,
          u.updated_at DESC
        """
    ).fetchall()

    for unit_id, hash_value, unit_xml in rows:
        if unit_id in existing_ids:
            continue
        unit = ET.fromstring(unit_xml)
        target_xml, origin = best_target_xml(conn, unit_id, hash_value)
        if target_xml:
            target = ET.fromstring(target_xml)
            target.set("state", "translated")
            if origin:
                target.set(f"{{{CP_NS}}}origin", origin)
            replace_or_insert_target(unit, target)
        else:
            replace_or_insert_target(unit, target_from_source(find_child(unit, "source")))
        body.append(unit)
        existing_ids.add(unit_id)
        added += 1
    return added


def build_from_db(args: argparse.Namespace) -> int:
    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)
    if args.locale_tag:
        args.target_language = args.locale_tag
    if args.output == Path("output/pt_BR.xlf"):
        output_name = f"{args.target_language}.custom.xlf" if args.locale_tag else "pt_BR.custom.xlf"
        args.output = Path("output") / output_name
    if args.locale_tag and args.extended_output == Path("output/pt_BR_extended.custom.xlf"):
        args.extended_output = Path("output") / f"{args.target_language}_extended.custom.xlf"

    with sqlite3.connect(args.db) as conn:
        ensure_db_tables(conn)
        canonical_tree = ET.parse(args.source)
        get_file(canonical_tree.getroot()).set("target-language", args.target_language)
        canonical_stats = apply_db_targets(canonical_tree, conn)
        canonical_plural_units_removed = 0
        if args.exclude_plurals:
            canonical_plural_units_removed = remove_plural_units(canonical_tree.getroot())

        args.output.parent.mkdir(parents=True, exist_ok=True)
        canonical_tree.write(args.output, encoding="utf-8", xml_declaration=True)

        extended_tree = ET.parse(args.output)
        added = append_extended_units(extended_tree, conn)
        extended_plural_units_removed = 0
        if args.exclude_plurals:
            extended_plural_units_removed = remove_plural_units(extended_tree.getroot())
        args.extended_output.parent.mkdir(parents=True, exist_ok=True)
        extended_tree.write(args.extended_output, encoding="utf-8", xml_declaration=True)

        config_path = None
        if args.locale_tag or args.fallback_locale:
            config_path = args.locale_config_output
            if config_path == Path("output/locale_config.json"):
                config_path = Path("output") / f"{args.target_language}.config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config = {
                "locale_tag": args.target_language,
                "fallback_locale": args.fallback_locale,
                "number_formatting": args.number_formatting,
                "character_orientation": args.character_orientation,
                "canonical_xlf": str(args.output),
                "extended_xlf": str(args.extended_output),
                "notes": [
                    "Create or copy this locale in WHM before upload.",
                    "Set fallback, number formatting, and character orientation in WHM's Copy/Edit Locale interface.",
                    "After upload, run /usr/local/cpanel/bin/build_locale_databases on the server.",
                ],
            }
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Canonical custom output: {args.output}")
    print(f"Extended custom output: {args.extended_output}")
    if args.locale_tag or args.fallback_locale:
        print(f"Locale tag: {args.target_language}")
        print(f"Fallback locale: {args.fallback_locale}")
        print(f"Locale config: {config_path}")
    print(f"Canonical translated: {canonical_stats['translated']}")
    print(f"Canonical pending: {canonical_stats['pending']}")
    print(f"Extended extra units added: {added}")
    if args.exclude_plurals:
        print(f"Canonical plural units/groups removed: {canonical_plural_units_removed}")
        print(f"Extended plural units/groups removed: {extended_plural_units_removed}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a final XLF locale file in output/.")
    parser.add_argument("input_xlf", type=Path, nargs="?", help="Validated custom XLF file.")
    parser.add_argument("--output", type=Path, default=Path("output/pt_BR.xlf"), help="Final output file.")
    parser.add_argument("--from-db", action="store_true", help="Build canonical and extended outputs from SQLite.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--source", type=Path, default=Path("locales/original/en.xlf"))
    parser.add_argument("--extended-output", type=Path, default=Path("output/pt_BR_extended.custom.xlf"))
    parser.add_argument("--target-language", default="pt_br")
    parser.add_argument("--locale-tag", help="Custom WHM locale tag, for example i_pt_br_enhanced.")
    parser.add_argument("--fallback-locale", default="pt_BR", help="Fallback locale to document for WHM setup.")
    parser.add_argument("--number-formatting", default="pt_BR", help="Number formatting locale to document for WHM setup.")
    parser.add_argument("--character-orientation", default="left-to-right", help="Character orientation to document for WHM setup.")
    parser.add_argument("--locale-config-output", type=Path, default=Path("output/locale_config.json"))
    parser.add_argument("--exclude-plurals", action="store_true", help="Remove XLIFF plural groups that contain x-implied placeholders.")
    args = parser.parse_args()

    try:
        if args.from_db:
            return build_from_db(args)
        if args.input_xlf is None:
            parser.error("input_xlf is required unless --from-db is used")
        return build_locale(args.input_xlf, args.output)
    except ET.ParseError as exc:
        print(f"Build failed: invalid XML: {exc}")
        return 1
    except RuntimeError as exc:
        print(f"Build failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
