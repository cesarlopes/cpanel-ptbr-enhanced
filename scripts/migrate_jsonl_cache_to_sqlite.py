"""Migrate ai_translations*.jsonl cache files into SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                translation TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id, source_hash, model)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                duration_ms INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def migrate(input_path: Path, db_path: Path) -> int:
    init_db(db_path)
    inserted = 0
    skipped = 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        for line_number, line in enumerate(input_path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO translations (id, source_hash, model, source, translation, status)
                    VALUES (?, ?, ?, '', ?, 'ok')
                    """,
                    (
                        item["id"],
                        item["source_hash"],
                        item["model"],
                        item["translation"],
                    ),
                )
                if conn.total_changes > inserted:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                skipped += 1
                print(f"Skipped line {line_number}: {exc}")

    print(f"Input: {input_path}")
    print(f"SQLite DB: {db_path}")
    print(f"Inserted: {inserted}")
    print(f"Skipped/duplicates: {skipped}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate JSONL translation cache to SQLite.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    args = parser.parse_args()
    return migrate(args.input, args.db)


if __name__ == "__main__":
    raise SystemExit(main())
