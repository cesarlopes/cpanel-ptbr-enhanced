"""Import XLF locale units and translation memories into SQLite.

This script turns exported cPanel locale files into a reviewable translation
memory. It intentionally keeps the existing `translations` table as the raw AI
API cache and stores review/build data in separate `locale_*` tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CP_NS = "tag:cpanel.net,2012-01:translate"
TOKEN_PREFIX = "__XLF_TAG_"
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
    unit_id: str
    source_hash: str
    datatype: str
    source_text: str
    source_tokenized: str
    source_xml: str
    target_text: str
    target_xml: str
    unit_xml: str
    unit_attrs: dict[str, str]
    target_attrs: dict[str, str]
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


def element_xml(element: ET.Element | None) -> str:
    return ET.tostring(element, encoding="unicode") if element is not None else ""


def element_fingerprint(element: ET.Element | None) -> Any:
    if element is None:
        return None
    return {
        "tag": element.tag,
        "attrs": sorted(element.attrib.items()),
        "text": element.text or "",
        "tail": element.tail or "",
        "children": [element_fingerprint(child) for child in list(element)],
    }


def stable_source_hash(source: ET.Element | None) -> str:
    payload = json.dumps(element_fingerprint(source), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tag_signature(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    return [local_name(child.tag) for child in element.iter() if child is not element]


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def tokenized_source(source: ET.Element | None) -> str:
    if source is None:
        return ""
    parts: list[str] = []
    children = list(source)
    if source.text:
        parts.append(source.text)
    for index, child in enumerate(children):
        parts.append(f"{TOKEN_PREFIX}{index}__")
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def load_units(path: Path) -> list[UnitRecord]:
    tree = ET.parse(path)
    seen: set[str] = set()
    records: list[UnitRecord] = []

    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        unit_id = unit.attrib.get("id")
        if not unit_id:
            continue
        if unit_id in seen:
            raise ValueError(f"duplicate trans-unit id in {path}: {unit_id}")
        seen.add(unit_id)

        source = find_child(unit, "source")
        target = find_child(unit, "target")
        source_xml = element_xml(source)
        records.append(
            UnitRecord(
                unit_id=unit_id,
                source_hash=stable_source_hash(source),
                datatype=unit.attrib.get("datatype", ""),
                source_text=text_content(source),
                source_tokenized=tokenized_source(source),
                source_xml=source_xml,
                target_text=text_content(target),
                target_xml=element_xml(target),
                unit_xml=element_xml(unit),
                unit_attrs=dict(unit.attrib),
                target_attrs=dict(target.attrib) if target is not None else {},
                source_tags=tag_signature(source),
                target_tags=tag_signature(target),
            )
        )
    return records


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS locale_imports (
            import_id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            language TEXT NOT NULL,
            unit_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS locale_units (
            unit_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            source_tokenized TEXT NOT NULL DEFAULT '',
            source_xml TEXT NOT NULL,
            unit_xml TEXT NOT NULL,
            unit_attrs_json TEXT NOT NULL DEFAULT '{}',
            datatype TEXT NOT NULL DEFAULT '',
            canonical INTEGER NOT NULL DEFAULT 0,
            extended INTEGER NOT NULL DEFAULT 1,
            first_origin TEXT NOT NULL DEFAULT '',
            first_file TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (unit_id, source_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_locale_units_scope
            ON locale_units(canonical, extended);

        CREATE TABLE IF NOT EXISTS locale_targets (
            target_id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            target TEXT NOT NULL,
            target_xml TEXT NOT NULL,
            target_attrs_json TEXT NOT NULL DEFAULT '{}',
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL,
            quality_status TEXT NOT NULL DEFAULT 'valid',
            is_reviewed INTEGER NOT NULL DEFAULT 0,
            source_file TEXT NOT NULL DEFAULT '',
            import_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (unit_id, source_hash, origin, provider, model, source_file)
        );

        CREATE INDEX IF NOT EXISTS idx_locale_targets_unit
            ON locale_targets(unit_id, source_hash);
        """
    )
    conn.commit()


def reset_locale_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS locale_targets;
        DROP TABLE IF EXISTS locale_units;
        DROP TABLE IF EXISTS locale_imports;
        """
    )
    conn.commit()


def target_quality(record: UnitRecord) -> str:
    if not record.target_xml or not record.target_text:
        return "missing"
    if record.target_text == record.source_text:
        return "source_equal"
    if placeholders(record.source_text) - placeholders(record.target_text):
        return "missing_placeholder"
    if record.source_tags != record.target_tags:
        return "tag_mismatch"
    return "valid"


def insert_import(conn: sqlite3.Connection, path: Path, kind: str, language: str, count: int) -> int:
    cursor = conn.execute(
        """
        INSERT INTO locale_imports (path, kind, language, unit_count)
        VALUES (?, ?, ?, ?)
        """,
        (str(path), kind, language, count),
    )
    return int(cursor.lastrowid)


def upsert_unit(conn: sqlite3.Connection, record: UnitRecord, *, canonical: bool, origin: str, path: Path) -> None:
    conn.execute(
        """
        INSERT INTO locale_units (
            unit_id, source_hash, source, source_tokenized, source_xml, unit_xml,
            unit_attrs_json, datatype, canonical, extended, first_origin, first_file
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(unit_id, source_hash) DO UPDATE SET
            canonical = CASE WHEN excluded.canonical = 1 THEN 1 ELSE locale_units.canonical END,
            extended = 1,
            source_tokenized = CASE
                WHEN locale_units.source_tokenized = '' THEN excluded.source_tokenized
                ELSE locale_units.source_tokenized
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            record.unit_id,
            record.source_hash,
            record.source_text,
            record.source_tokenized,
            record.source_xml,
            record.unit_xml,
            json.dumps(record.unit_attrs, ensure_ascii=False, sort_keys=True),
            record.datatype,
            1 if canonical else 0,
            origin,
            str(path),
        ),
    )


def upsert_target(
    conn: sqlite3.Connection,
    record: UnitRecord,
    *,
    origin: str,
    provider: str,
    model: str,
    quality_status: str,
    source_file: str,
    import_id: int | None,
    is_reviewed: bool = False,
    target_text: str | None = None,
    target_xml: str | None = None,
    target_attrs: dict[str, str] | None = None,
) -> None:
    if target_xml is None:
        target_xml = record.target_xml
    if not target_xml:
        return
    conn.execute(
        """
        INSERT INTO locale_targets (
            unit_id, source_hash, target, target_xml, target_attrs_json, provider,
            model, origin, quality_status, is_reviewed, source_file, import_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(unit_id, source_hash, origin, provider, model, source_file)
        DO UPDATE SET
            target = excluded.target,
            target_xml = excluded.target_xml,
            target_attrs_json = excluded.target_attrs_json,
            quality_status = excluded.quality_status,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            record.unit_id,
            record.source_hash,
            target_text if target_text is not None else record.target_text,
            target_xml,
            json.dumps(target_attrs if target_attrs is not None else record.target_attrs, ensure_ascii=False, sort_keys=True),
            provider,
            model,
            origin,
            quality_status,
            1 if is_reviewed else 0,
            source_file,
            import_id,
        ),
    )


def import_file(conn: sqlite3.Connection, path: Path, *, kind: str, language: str, canonical: bool) -> dict[str, int]:
    records = load_units(path)
    import_id = insert_import(conn, path, kind, language, len(records))
    imported_targets = 0

    for record in records:
        upsert_unit(conn, record, canonical=canonical, origin=kind, path=path)
        if kind in {"translated", "custom"} and record.target_xml:
            upsert_target(
                conn,
                record,
                origin="manual" if kind == "custom" else "cpanel",
                provider="human" if kind == "custom" else "cpanel",
                model="",
                quality_status=target_quality(record),
                source_file=str(path),
                import_id=import_id,
                is_reviewed=kind == "custom",
            )
            imported_targets += 1

    return {"units": len(records), "targets": imported_targets}


def build_target_from_ai(record: UnitRecord, translation: str) -> ET.Element:
    source = ET.fromstring(record.source_xml)
    target = ET.Element(f"{{{XLIFF_NS}}}target")
    children = list(source)
    if not children:
        target.text = translation
        return target

    pieces: list[str] = []
    last = 0
    found: list[int] = []
    marker = TOKEN_PREFIX
    index = translation.find(marker)
    while index != -1:
        pieces.append(translation[last:index])
        end = translation.find("__", index + len(marker))
        if end == -1:
            raise ValueError("unterminated inline token")
        token_number = translation[index + len(marker) : end]
        found.append(int(token_number))
        last = end + 2
        index = translation.find(marker, last)
    pieces.append(translation[last:])

    expected = list(range(len(children)))
    if found != expected:
        raise ValueError(f"inline token mismatch: expected {expected}, got {found}")

    target.text = pieces[0]
    for piece_index, child_index in enumerate(found):
        child = deepcopy(children[child_index])
        child.tail = pieces[piece_index + 1]
        target.append(child)
    return target


def import_ai_cache(conn: sqlite3.Connection) -> dict[str, int]:
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'translations'"
    ).fetchone()
    if existing is None:
        return {"imported": 0, "skipped": 0}

    imported = 0
    skipped = 0
    rows = conn.execute(
        """
        SELECT id, model, source, translation
        FROM translations
        WHERE status = 'ok'
        ORDER BY model, id
        """
    ).fetchall()

    for unit_id, model, cached_source, translation in rows:
        unit_rows = conn.execute(
            """
            SELECT unit_id, source_hash, datatype, source, source_tokenized, source_xml,
                   '' AS target, '' AS target_xml, unit_xml, unit_attrs_json
            FROM locale_units
            WHERE unit_id = ? AND (source_tokenized = ? OR source = ?)
            """,
            (unit_id, cached_source, cached_source),
        ).fetchall()
        if not unit_rows:
            skipped += 1
            continue

        for row in unit_rows:
            unit_attrs = json.loads(row[9] or "{}")
            record = UnitRecord(
                unit_id=row[0],
                source_hash=row[1],
                datatype=row[2],
                source_text=row[3],
                source_tokenized=row[4],
                source_xml=row[5],
                target_text=translation,
                target_xml="",
                unit_xml=row[8],
                unit_attrs=unit_attrs,
                target_attrs={},
                source_tags=[],
                target_tags=[],
            )
            try:
                target = build_target_from_ai(record, translation)
            except Exception:
                skipped += 1
                continue
            target.set("state", "translated")
            target.set(f"{{{CP_NS}}}translated-by", f"ai_cache:{model}")
            upsert_target(
                conn,
                record,
                origin="ai_cache",
                provider="ai",
                model=model,
                quality_status="valid",
                source_file="cache/translations.sqlite",
                import_id=None,
                target_text=text_content(target),
                target_xml=element_xml(target),
                target_attrs=dict(target.attrib),
            )
            imported += 1

    return {"imported": imported, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import cPanel XLF locale files into SQLite review tables.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--original", type=Path, default=Path("locales/original/en.xlf"))
    parser.add_argument("--translated", type=Path, nargs="*", default=[Path("locales/translated/pt_br.xlf"), Path("locales/translated/pt_br_2.xlf")])
    parser.add_argument("--custom", type=Path, nargs="*", default=[])
    parser.add_argument("--language", default="pt_BR")
    parser.add_argument("--skip-ai-cache", action="store_true")
    parser.add_argument("--no-reset", action="store_true", help="Keep existing locale_* rows and upsert into them.")
    args = parser.parse_args()

    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db) as conn:
        if not args.no_reset:
            reset_locale_tables(conn)
        ensure_schema(conn)

        original_stats = import_file(conn, args.original, kind="original", language="en", canonical=True)
        translated_stats = [
            (path, import_file(conn, path, kind="translated", language=args.language, canonical=False))
            for path in args.translated
            if path.exists()
        ]
        custom_stats = [
            (path, import_file(conn, path, kind="custom", language=args.language, canonical=False))
            for path in args.custom
            if path.exists()
        ]
        ai_stats = {"imported": 0, "skipped": 0}
        if not args.skip_ai_cache:
            ai_stats = import_ai_cache(conn)
        conn.commit()

    print(f"Database: {args.db}")
    print(f"Original units imported: {original_stats['units']}")
    for path, stats in translated_stats:
        print(f"Translated file imported: {path} units={stats['units']} targets={stats['targets']}")
    for path, stats in custom_stats:
        print(f"Custom file imported: {path} units={stats['units']} targets={stats['targets']}")
    print(f"AI cache targets imported: {ai_stats['imported']}")
    print(f"AI cache rows skipped: {ai_stats['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
