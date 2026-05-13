"""Prepare canonical and extended pt_BR locale files without machine translation.

The canonical output uses the current English export as the source of truth.
Existing pt_BR/custom exports are treated as translation memories: their
targets are reused only when they match the canonical source and pass basic
placeholder/tag checks.

No AI or machine translation is executed here. Untranslated units keep the
English source structure in target and are marked as needs-translation.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CP_NS = "tag:cpanel.net,2012-01:translate"
PLACEHOLDER_RE = re.compile(
    r"""
    \[_\d+\]                 |
    %[sd]                    |
    \{\{[A-Za-z_][\w.-]*\}\} |
    \{[A-Za-z_][\w.-]*\}     |
    :[A-Za-z_][\w.-]*
    """,
    re.VERBOSE,
)


@dataclass
class UnitRecord:
    unit: ET.Element
    source: ET.Element | None
    target: ET.Element | None
    source_text: str
    target_text: str
    source_tags: list[str]
    target_tags: list[str]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_child(unit: ET.Element, child_name: str) -> ET.Element | None:
    for child in unit:
        if local_name(child.tag) == child_name:
            return child
    return None


def text_content(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def tag_signature(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    return [local_name(child.tag) for child in element.iter() if child is not element]


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def target_is_usable(canonical: UnitRecord, candidate: UnitRecord) -> tuple[bool, str]:
    if candidate.target is None:
        return False, "missing target"
    if not candidate.target_text:
        return False, "empty target"
    if candidate.source_text != canonical.source_text:
        return False, "source text differs"
    if candidate.target_text == canonical.source_text:
        return False, "target equals source"
    if placeholders(canonical.source_text) - placeholders(candidate.target_text):
        return False, "missing placeholder"
    if canonical.source_tags != candidate.target_tags:
        return False, "inline tag mismatch"
    return True, "ok"


def load_units(path: Path) -> dict[str, UnitRecord]:
    tree = ET.parse(path)
    units: dict[str, UnitRecord] = {}

    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        unit_id = unit.attrib.get("id")
        if not unit_id:
            continue

        source = find_child(unit, "source")
        target = find_child(unit, "target")
        units[unit_id] = UnitRecord(
            unit=unit,
            source=source,
            target=target,
            source_text=text_content(source),
            target_text=text_content(target),
            source_tags=tag_signature(source),
            target_tags=tag_signature(target),
        )

    return units


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
        children = list(unit)
        index = children.index(source)
        unit.insert(index + 1, new_target)
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


def translated_target(candidate: UnitRecord, origin: str) -> ET.Element:
    assert candidate.target is not None
    target = deepcopy(candidate.target)
    target.set("state", "translated")
    target.set(f"{{{CP_NS}}}origin", origin)
    return target


def choose_target(
    unit_id: str,
    canonical: UnitRecord,
    memories: list[tuple[str, dict[str, UnitRecord]]],
) -> tuple[ET.Element, dict[str, Any]]:
    rejected: list[dict[str, str]] = []

    for name, units in memories:
        candidate = units.get(unit_id)
        if candidate is None:
            continue

        ok, reason = target_is_usable(canonical, candidate)
        if ok:
            return translated_target(candidate, name), {
                "id": unit_id,
                "status": "reused",
                "origin": name,
            }
        rejected.append({"origin": name, "reason": reason})

    return target_from_source(canonical.source), {
        "id": unit_id,
        "status": "pending",
        "reason": "no usable existing target",
        "rejected": rejected,
    }


def extra_unit_is_usable(record: UnitRecord) -> bool:
    if record.source is None or record.target is None:
        return False
    if not record.source_text or not record.target_text:
        return False
    if record.source_text == record.target_text:
        return False
    if placeholders(record.source_text) - placeholders(record.target_text):
        return False
    if record.source_tags != record.target_tags:
        return False
    return True


def append_extra_units(
    body: ET.Element,
    canonical_ids: set[str],
    memories: list[tuple[str, dict[str, UnitRecord]]],
) -> list[dict[str, str]]:
    added: list[dict[str, str]] = []
    seen = set(canonical_ids)

    for name, units in memories:
        for unit_id in sorted(units):
            if unit_id in seen:
                continue

            record = units[unit_id]
            if not extra_unit_is_usable(record):
                continue

            extra = deepcopy(record.unit)
            extra.set(f"{{{CP_NS}}}origin", name)
            target = find_child(extra, "target")
            if target is not None:
                target.set("state", "translated")
                target.set(f"{{{CP_NS}}}origin", name)
            body.append(extra)
            seen.add(unit_id)
            added.append({"id": unit_id, "origin": name})

    return added


def prepare(args: argparse.Namespace) -> int:
    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)

    canonical_tree = ET.parse(args.source)
    canonical_root = canonical_tree.getroot()
    get_file(canonical_root).set("target-language", args.target_language)

    canonical_units = load_units(args.source)
    memories = [(path.stem, load_units(path)) for path in args.memory if path.exists()]

    report: dict[str, Any] = {
        "source": str(args.source),
        "target_language": args.target_language,
        "memories": [name for name, _units in memories],
        "reused": [],
        "pending": [],
        "extended_added": [],
    }

    for unit in canonical_root.iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        unit_id = unit.attrib.get("id")
        if not unit_id:
            continue

        target, item_report = choose_target(unit_id, canonical_units[unit_id], memories)
        replace_or_insert_target(unit, target)
        report[item_report["status"]].append(item_report)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    canonical_tree.write(args.output, encoding="utf-8", xml_declaration=True)

    extended_tree = ET.parse(args.output)
    extended_root = extended_tree.getroot()
    extended_body = get_body(extended_root)
    report["extended_added"] = append_extra_units(extended_body, set(canonical_units), memories)

    args.extended_output.parent.mkdir(parents=True, exist_ok=True)
    extended_tree.write(args.extended_output, encoding="utf-8", xml_declaration=True)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Canonical output: {args.output}")
    print(f"Extended output: {args.extended_output}")
    print(f"Memory files used: {len(memories)}")
    print(f"Reused targets: {len(report['reused'])}")
    print(f"Pending targets: {len(report['pending'])}")
    print(f"Extended extra units added: {len(report['extended_added'])}")
    if args.report:
        print(f"Report: {args.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare pt_BR canonical and extended XLF files.")
    parser.add_argument("--source", type=Path, default=Path("locales/original/en.xlf"))
    parser.add_argument(
        "--memory",
        type=Path,
        nargs="+",
        default=[
            Path("locales/custom/i_pt_br_custom.xlf"),
            Path("locales/translated/pt_br.xlf"),
            Path("locales/translated/pt_br_2.xlf"),
        ],
        help="Existing XLF files to reuse as translation memories, in priority order.",
    )
    parser.add_argument("--target-language", default="pt_br")
    parser.add_argument("--output", type=Path, default=Path("output/pt_BR.xlf"))
    parser.add_argument("--extended-output", type=Path, default=Path("output/pt_BR_extended.xlf"))
    parser.add_argument("--report", type=Path, default=Path("cache/prepare_v1_report.json"))
    args = parser.parse_args()

    return prepare(args)


if __name__ == "__main__":
    raise SystemExit(main())
