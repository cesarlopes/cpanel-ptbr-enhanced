"""Create review/UI indexes and refresh SQLite planner statistics.

Run this after large imports, migrations, or translation batches if the PHP UI
starts feeling slow. The script is safe to run multiple times.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from refresh_locale_status import ensure_status_schema


INDEXES = [
    (
        "idx_locale_units_scope",
        "CREATE INDEX IF NOT EXISTS idx_locale_units_scope "
        "ON locale_units(canonical, extended)",
    ),
    (
        "idx_locale_units_canonical_order",
        "CREATE INDEX IF NOT EXISTS idx_locale_units_canonical_order "
        "ON locale_units(canonical, unit_id, updated_at, source_hash)",
    ),
    (
        "idx_locale_units_extended_order",
        "CREATE INDEX IF NOT EXISTS idx_locale_units_extended_order "
        "ON locale_units(extended, canonical, unit_id, updated_at, source_hash)",
    ),
    (
        "idx_locale_targets_unit",
        "CREATE INDEX IF NOT EXISTS idx_locale_targets_unit "
        "ON locale_targets(unit_id, source_hash)",
    ),
    (
        "idx_locale_targets_best",
        "CREATE INDEX IF NOT EXISTS idx_locale_targets_best "
        "ON locale_targets(unit_id, source_hash, quality_status, origin, is_reviewed, updated_at, target_id)",
    ),
    (
        "idx_locale_targets_origin_quality",
        "CREATE INDEX IF NOT EXISTS idx_locale_targets_origin_quality "
        "ON locale_targets(origin, quality_status, unit_id, source_hash)",
    ),
    (
        "idx_locale_targets_reviewed",
        "CREATE INDEX IF NOT EXISTS idx_locale_targets_reviewed "
        "ON locale_targets(is_reviewed, quality_status, unit_id, source_hash)",
    ),
    (
        "idx_locale_targets_unit_origin",
        "CREATE INDEX IF NOT EXISTS idx_locale_targets_unit_origin "
        "ON locale_targets(unit_id, origin, quality_status, updated_at, target_id)",
    ),
]


def ensure_tables(conn: sqlite3.Connection) -> None:
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('locale_units', 'locale_targets')"
        )
    }
    missing = {"locale_units", "locale_targets"} - existing
    if missing:
        raise RuntimeError(
            "missing locale DB tables: "
            + ", ".join(sorted(missing))
            + ". Run scripts/import_locale_to_db.py first."
        )


def optimize(db_path: Path) -> None:
    started = time.perf_counter()
    conn = sqlite3.connect(db_path)
    try:
        ensure_tables(conn)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cache_size = -64000")
        ensure_status_schema(conn)

        for name, sql in INDEXES:
            index_started = time.perf_counter()
            conn.execute(sql)
            conn.commit()
            elapsed = time.perf_counter() - index_started
            print(f"Index ready: {name} ({elapsed:.2f}s)")

        analyze_started = time.perf_counter()
        conn.execute("ANALYZE")
        conn.execute("PRAGMA optimize")
        conn.commit()
        print(f"Planner stats refreshed ({time.perf_counter() - analyze_started:.2f}s)")
    finally:
        conn.close()

    print(f"Optimized: {db_path} ({time.perf_counter() - started:.2f}s)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize the SQLite locale database for UI/review queries.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    args = parser.parse_args()

    if not args.db.exists():
        parser.error(f"database not found: {args.db}")

    optimize(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
