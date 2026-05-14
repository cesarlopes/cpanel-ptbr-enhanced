"""Import reviewed manual translations from JSONL into SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from refresh_locale_status import refresh_status


def ensure_locale_tables(conn: sqlite3.Connection) -> None:
    missing = [
        name
        for name in ("locale_units", "locale_targets")
        if conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        is None
    ]
    if missing:
        raise RuntimeError("missing locale DB tables: " + ", ".join(missing) + ". Run scripts/import_locale_to_db.py first.")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            items.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on {path}:{line_number}: {exc}") from exc
    return items


def import_reviewed_targets(db_path: Path, input_path: Path) -> int:
    items = load_jsonl(input_path)
    imported = 0
    skipped = 0

    with sqlite3.connect(db_path) as conn:
        ensure_locale_tables(conn)
        for item in items:
            unit_id = item.get("unit_id", "")
            source_hash = item.get("source_hash", "")
            target = item.get("target", "")
            target_xml = item.get("target_xml", "")
            if not unit_id or not source_hash or not target or not target_xml:
                skipped += 1
                continue

            exists = conn.execute(
                "SELECT 1 FROM locale_units WHERE unit_id = ? AND source_hash = ?",
                (unit_id, source_hash),
            ).fetchone()
            if exists is None:
                skipped += 1
                continue

            target_attrs = item.get("target_attrs", {})
            conn.execute(
                """
                INSERT INTO locale_targets (
                    unit_id, source_hash, target, target_xml, target_attrs_json,
                    provider, model, origin, quality_status, is_reviewed, source_file
                )
                VALUES (?, ?, ?, ?, ?, 'human', '', 'manual', ?, 1, ?)
                ON CONFLICT(unit_id, source_hash, origin, provider, model, source_file)
                DO UPDATE SET
                    target = excluded.target,
                    target_xml = excluded.target_xml,
                    target_attrs_json = excluded.target_attrs_json,
                    quality_status = excluded.quality_status,
                    is_reviewed = 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    unit_id,
                    source_hash,
                    target,
                    target_xml,
                    json.dumps(target_attrs, ensure_ascii=False, sort_keys=True),
                    item.get("quality_status", "valid"),
                    str(input_path),
                ),
            )
            imported += 1
        status_rows = refresh_status(conn)
        conn.commit()

    print(f"Imported reviewed manual targets: {imported}")
    print(f"Skipped: {skipped}")
    print(f"Status rows refreshed: {status_rows}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import reviewed manual translations from JSONL.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--input", type=Path, default=Path("data/manual_targets.jsonl"))
    args = parser.parse_args()

    return import_reviewed_targets(args.db, args.input)


if __name__ == "__main__":
    raise SystemExit(main())
