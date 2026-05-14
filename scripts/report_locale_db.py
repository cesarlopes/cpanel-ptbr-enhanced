"""Report translation coverage from the SQLite locale review tables."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0)


def ensure_tables(conn: sqlite3.Connection) -> None:
    missing = [
        name
        for name in ("locale_units", "locale_targets", "locale_imports")
        if conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        is None
    ]
    if missing:
        raise RuntimeError("missing locale DB tables: " + ", ".join(missing))


def print_scope(conn: sqlite3.Connection, label: str, where_clause: str) -> None:
    total = scalar(conn, f"SELECT COUNT(DISTINCT u.unit_id) FROM locale_units u WHERE {where_clause}")
    cpanel = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT u.unit_id)
        FROM locale_units u
        JOIN locale_targets t ON t.unit_id = u.unit_id AND t.source_hash = u.source_hash
        WHERE {where_clause} AND t.origin = 'cpanel' AND t.quality_status = 'valid'
        """,
    )
    ai = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT u.unit_id)
        FROM locale_units u
        JOIN locale_targets t ON t.unit_id = u.unit_id AND t.source_hash = u.source_hash
        WHERE {where_clause} AND t.origin = 'ai_cache' AND t.quality_status = 'valid'
        """,
    )
    reviewed = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT u.unit_id)
        FROM locale_units u
        JOIN locale_targets t ON t.unit_id = u.unit_id AND t.source_hash = u.source_hash
        WHERE {where_clause} AND t.is_reviewed = 1 AND t.quality_status IN ('valid', 'approved')
        """,
    )
    ready = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT u.unit_id)
        FROM locale_units u
        JOIN locale_targets t ON t.unit_id = u.unit_id AND t.source_hash = u.source_hash
        WHERE {where_clause}
          AND t.quality_status IN ('valid', 'approved')
          AND t.origin IN ('manual', 'ai_cache', 'cpanel')
        """,
    )
    pending = max(total - ready, 0)

    print(label)
    print(f"  total: {total}")
    print(f"  ready: {ready}")
    print(f"  pending: {pending}")
    print(f"  cpanel valid: {cpanel}")
    print(f"  ai cache valid: {ai}")
    print(f"  reviewed: {reviewed}")


def report(conn: sqlite3.Connection) -> None:
    ensure_tables(conn)
    print_scope(conn, "Canonical", "u.canonical = 1")
    print_scope(conn, "Extended", "u.extended = 1")

    conflicts = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM (
            SELECT unit_id
            FROM locale_units
            GROUP BY unit_id
            HAVING COUNT(DISTINCT source_hash) > 1
        )
        """,
    )
    print("Conflicts")
    print(f"  ids with multiple sources: {conflicts}")

    print("Imports")
    for row in conn.execute(
        """
        SELECT import_id, kind, language, unit_count, path, imported_at
        FROM locale_imports
        ORDER BY import_id
        """
    ):
        print(f"  #{row[0]} {row[1]} {row[2]} units={row[3]} {row[4]} at {row[5]}")

    print("Targets by origin")
    for origin, quality, count in conn.execute(
        """
        SELECT origin, quality_status, COUNT(*)
        FROM locale_targets
        GROUP BY origin, quality_status
        ORDER BY origin, quality_status
        """
    ):
        print(f"  {origin}/{quality}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show locale translation coverage from SQLite.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    args = parser.parse_args()

    try:
        with sqlite3.connect(args.db) as conn:
            report(conn)
    except RuntimeError as exc:
        print(f"Report failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
