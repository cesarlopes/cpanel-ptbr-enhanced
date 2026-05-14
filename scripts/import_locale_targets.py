"""Import locale target snapshots from JSONL or JSONL.GZ into SQLite."""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, TextIO

from refresh_locale_status import refresh_status


def open_text_reader(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8-sig")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with open_text_reader(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on {path}:{line_number}: {exc}") from exc


def ensure_locale_tables(conn: sqlite3.Connection) -> None:
    missing = [
        name
        for name in ("locale_units", "locale_targets")
        if conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        is None
    ]
    if missing:
        raise RuntimeError("missing locale DB tables: " + ", ".join(missing) + ". Run scripts/import_locale_to_db.py first.")


def import_targets(db_path: Path, input_path: Path, overwrite: bool) -> int:
    imported = 0
    skipped = 0

    with sqlite3.connect(db_path) as conn:
        ensure_locale_tables(conn)
        for item in iter_jsonl(input_path):
            unit_id = item.get("unit_id", "")
            source_hash = item.get("source_hash", "")
            target = item.get("target", "")
            target_xml = item.get("target_xml", "")
            origin = item.get("origin", "")
            provider = item.get("provider", "")
            model = item.get("model", "")
            source_file = item.get("source_file", str(input_path))

            if not unit_id or not source_hash or not target or not target_xml or not origin:
                skipped += 1
                continue

            exists = conn.execute(
                "SELECT 1 FROM locale_units WHERE unit_id = ? AND source_hash = ?",
                (unit_id, source_hash),
            ).fetchone()
            if exists is None:
                skipped += 1
                continue

            sql = (
                """
                INSERT INTO locale_targets (
                    unit_id, source_hash, target, target_xml, target_attrs_json,
                    provider, model, origin, quality_status, is_reviewed, source_file
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id, source_hash, origin, provider, model, source_file)
                DO UPDATE SET
                    target = excluded.target,
                    target_xml = excluded.target_xml,
                    target_attrs_json = excluded.target_attrs_json,
                    quality_status = excluded.quality_status,
                    is_reviewed = excluded.is_reviewed,
                    updated_at = CURRENT_TIMESTAMP
                """
                if overwrite
                else """
                INSERT OR IGNORE INTO locale_targets (
                    unit_id, source_hash, target, target_xml, target_attrs_json,
                    provider, model, origin, quality_status, is_reviewed, source_file
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
            )
            conn.execute(
                sql,
                (
                    unit_id,
                    source_hash,
                    target,
                    target_xml,
                    json.dumps(item.get("target_attrs", {}), ensure_ascii=False, sort_keys=True),
                    provider,
                    model,
                    origin,
                    item.get("quality_status", "valid"),
                    1 if item.get("is_reviewed") else 0,
                    source_file,
                ),
            )
            imported += 1
        status_rows = refresh_status(conn)
        conn.commit()

    print(f"Imported locale targets: {imported}")
    print(f"Skipped: {skipped}")
    print(f"Overwrite: {overwrite}")
    print(f"Status rows refreshed: {status_rows}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import locale_targets from JSONL or JSONL.GZ.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--input", type=Path, default=Path("data/locale_targets_snapshot.jsonl.gz"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    return import_targets(args.db, args.input, args.overwrite)


if __name__ == "__main__":
    raise SystemExit(main())
