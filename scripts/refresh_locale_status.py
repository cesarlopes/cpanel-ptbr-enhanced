"""Refresh the materialized locale status table used by the review UI.

`locale_units` and `locale_targets` remain the source of truth. This table is a
read-optimized snapshot that stores the current best target and display row for
each unit/source version.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path


STATUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS locale_unit_status (
    unit_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    source_xml TEXT NOT NULL,
    datatype TEXT NOT NULL DEFAULT '',
    canonical INTEGER NOT NULL DEFAULT 0,
    extended INTEGER NOT NULL DEFAULT 1,
    current_canonical INTEGER NOT NULL DEFAULT 0,
    current_extended INTEGER NOT NULL DEFAULT 0,
    target_id INTEGER,
    target TEXT,
    target_xml TEXT,
    origin TEXT,
    provider TEXT,
    model TEXT,
    quality_status TEXT,
    is_reviewed INTEGER NOT NULL DEFAULT 0,
    ready INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    target_updated_at TEXT,
    unit_updated_at TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (unit_id, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_locale_unit_status_extended
    ON locale_unit_status(current_extended, canonical, unit_id, source_hash);

CREATE INDEX IF NOT EXISTS idx_locale_unit_status_canonical
    ON locale_unit_status(current_canonical, unit_id, source_hash);

CREATE INDEX IF NOT EXISTS idx_locale_unit_status_status
    ON locale_unit_status(status, current_extended, current_canonical);

CREATE INDEX IF NOT EXISTS idx_locale_unit_status_origin
    ON locale_unit_status(origin, current_extended, current_canonical);

CREATE INDEX IF NOT EXISTS idx_locale_unit_status_reviewed
    ON locale_unit_status(reviewed, current_extended, current_canonical);
"""


REFRESH_SQL = """
WITH best_targets AS (
    SELECT *
    FROM (
        SELECT
            t.target_id,
            t.unit_id,
            t.source_hash,
            t.target,
            t.target_xml,
            t.origin,
            t.provider,
            t.model,
            t.quality_status,
            t.is_reviewed,
            t.updated_at,
            ROW_NUMBER() OVER (
                PARTITION BY t.unit_id, t.source_hash
                ORDER BY
                    CASE
                        WHEN t.origin = 'manual' AND t.is_reviewed = 1 THEN 1
                        WHEN t.origin = 'ai_cache' AND t.quality_status = 'approved' THEN 2
                        WHEN t.origin = 'ai_cache' THEN 3
                        WHEN t.origin = 'cpanel' THEN 4
                        ELSE 5
                    END,
                    t.updated_at DESC,
                    t.target_id DESC
            ) AS rn
        FROM locale_targets t
        WHERE t.quality_status IN ('valid', 'approved')
          AND t.origin IN ('manual', 'ai_cache', 'cpanel')
    )
    WHERE rn = 1
),
canonical_rank AS (
    SELECT
        u.unit_id,
        u.source_hash,
        ROW_NUMBER() OVER (
            PARTITION BY u.unit_id
            ORDER BY
                CASE WHEN bt.target_id IS NOT NULL THEN 0 ELSE 1 END,
                u.updated_at DESC,
                u.source_hash DESC
        ) AS rn
    FROM locale_units u
    LEFT JOIN best_targets bt
      ON bt.unit_id = u.unit_id
     AND bt.source_hash = u.source_hash
    WHERE u.canonical = 1
),
extended_rank AS (
    SELECT
        u.unit_id,
        u.source_hash,
        ROW_NUMBER() OVER (
            PARTITION BY u.unit_id
            ORDER BY
                u.canonical DESC,
                CASE WHEN bt.target_id IS NOT NULL THEN 0 ELSE 1 END,
                u.updated_at DESC,
                u.source_hash DESC
        ) AS rn
    FROM locale_units u
    LEFT JOIN best_targets bt
      ON bt.unit_id = u.unit_id
     AND bt.source_hash = u.source_hash
    WHERE u.extended = 1
)
INSERT INTO locale_unit_status (
    unit_id,
    source_hash,
    source,
    source_xml,
    datatype,
    canonical,
    extended,
    current_canonical,
    current_extended,
    target_id,
    target,
    target_xml,
    origin,
    provider,
    model,
    quality_status,
    is_reviewed,
    ready,
    reviewed,
    status,
    target_updated_at,
    unit_updated_at,
    refreshed_at
)
SELECT
    u.unit_id,
    u.source_hash,
    u.source,
    u.source_xml,
    u.datatype,
    u.canonical,
    u.extended,
    CASE WHEN cr.rn = 1 THEN 1 ELSE 0 END AS current_canonical,
    CASE WHEN er.rn = 1 THEN 1 ELSE 0 END AS current_extended,
    bt.target_id,
    bt.target,
    bt.target_xml,
    bt.origin,
    bt.provider,
    bt.model,
    bt.quality_status,
    COALESCE(bt.is_reviewed, 0),
    CASE WHEN bt.target_id IS NOT NULL THEN 1 ELSE 0 END AS ready,
    CASE WHEN COALESCE(bt.is_reviewed, 0) = 1 THEN 1 ELSE 0 END AS reviewed,
    COALESCE(bt.origin, 'pending') AS status,
    bt.updated_at,
    u.updated_at,
    CURRENT_TIMESTAMP
FROM locale_units u
LEFT JOIN best_targets bt
  ON bt.unit_id = u.unit_id
 AND bt.source_hash = u.source_hash
LEFT JOIN canonical_rank cr
  ON cr.unit_id = u.unit_id
 AND cr.source_hash = u.source_hash
LEFT JOIN extended_rank er
  ON er.unit_id = u.unit_id
 AND er.source_hash = u.source_hash
"""


def ensure_source_tables(conn: sqlite3.Connection) -> None:
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


def ensure_status_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(STATUS_SCHEMA)
    conn.commit()


def refresh_status(conn: sqlite3.Connection, unit_id: str | None = None, ensure_schema: bool = True) -> int:
    if ensure_schema:
        ensure_source_tables(conn)
        ensure_status_schema(conn)

    if unit_id:
        return refresh_unit_status(conn, unit_id)
    else:
        conn.execute("DELETE FROM locale_unit_status")
        conn.execute(REFRESH_SQL)
    conn.commit()

    if unit_id:
        row = conn.execute("SELECT COUNT(*) FROM locale_unit_status WHERE unit_id = ?", (unit_id,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM locale_unit_status").fetchone()
    return int(row[0] or 0)


def refresh_unit_status(conn: sqlite3.Connection, unit_id: str) -> int:
    rows = conn.execute(
        """
        SELECT unit_id, source_hash, source, source_xml, datatype, canonical, extended, updated_at
        FROM locale_units
        WHERE unit_id = ?
        """,
        (unit_id,),
    ).fetchall()

    prepared: list[dict[str, object]] = []
    for row in rows:
        target = conn.execute(
            """
            SELECT
                target_id,
                target,
                target_xml,
                origin,
                provider,
                model,
                quality_status,
                is_reviewed,
                updated_at
            FROM locale_targets
            WHERE unit_id = ?
              AND source_hash = ?
              AND quality_status IN ('valid', 'approved')
              AND origin IN ('manual', 'ai_cache', 'cpanel')
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
            (row[0], row[1]),
        ).fetchone()
        prepared.append(
            {
                "unit_id": row[0],
                "source_hash": row[1],
                "source": row[2],
                "source_xml": row[3],
                "datatype": row[4],
                "canonical": int(row[5] or 0),
                "extended": int(row[6] or 0),
                "unit_updated_at": row[7],
                "target": target,
            }
        )

    def rank_key(item: dict[str, object], prefer_canonical: bool) -> tuple[int, int, str, str]:
        return (
            int(item["canonical"]) if prefer_canonical else 0,
            1 if item["target"] is not None else 0,
            str(item["unit_updated_at"] or ""),
            str(item["source_hash"] or ""),
        )

    canonical_current = {
        str(max([item for item in prepared if item["canonical"]], key=lambda item: rank_key(item, False))["source_hash"])
    } if any(item["canonical"] for item in prepared) else set()
    extended_current = {
        str(max([item for item in prepared if item["extended"]], key=lambda item: rank_key(item, True))["source_hash"])
    } if any(item["extended"] for item in prepared) else set()

    conn.execute("DELETE FROM locale_unit_status WHERE unit_id = ?", (unit_id,))
    for item in prepared:
        target = item["target"]
        ready = 1 if target is not None else 0
        reviewed = int(target[7] or 0) if target is not None else 0
        conn.execute(
            """
            INSERT INTO locale_unit_status (
                unit_id, source_hash, source, source_xml, datatype,
                canonical, extended, current_canonical, current_extended,
                target_id, target, target_xml, origin, provider, model,
                quality_status, is_reviewed, ready, reviewed, status,
                target_updated_at, unit_updated_at, refreshed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                item["unit_id"],
                item["source_hash"],
                item["source"],
                item["source_xml"],
                item["datatype"],
                item["canonical"],
                item["extended"],
                1 if item["source_hash"] in canonical_current else 0,
                1 if item["source_hash"] in extended_current else 0,
                target[0] if target is not None else None,
                target[1] if target is not None else None,
                target[2] if target is not None else None,
                target[3] if target is not None else None,
                target[4] if target is not None else None,
                target[5] if target is not None else None,
                target[6] if target is not None else None,
                reviewed,
                ready,
                reviewed,
                target[3] if target is not None else "pending",
                target[8] if target is not None else None,
                item["unit_updated_at"],
            ),
        )
    conn.commit()
    return len(prepared)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh materialized locale status rows for the PHP UI.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--unit-id", help="Refresh only one unit id.")
    args = parser.parse_args()

    if not args.db.exists():
        parser.error(f"database not found: {args.db}")

    started = time.perf_counter()
    with sqlite3.connect(args.db) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA temp_store = MEMORY")
        count = refresh_status(conn, args.unit_id)
        conn.execute("ANALYZE locale_unit_status")
        conn.execute("PRAGMA optimize")
        conn.commit()

    scope = f"unit {args.unit_id}" if args.unit_id else "all units"
    print(f"Refreshed locale_unit_status for {scope}: {count} rows in {time.perf_counter() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
