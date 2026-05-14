"""Export reviewed manual translations from SQLite to JSONL.

The SQLite database is local working state and can be large. This script exports
only reviewed manual targets, which are small, important, and suitable for Git.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def export_reviewed_targets(db_path: Path, output_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                t.unit_id,
                t.source_hash,
                u.source,
                u.source_xml,
                t.target,
                t.target_xml,
                t.target_attrs_json,
                t.quality_status,
                t.updated_at
            FROM locale_targets t
            JOIN locale_units u
              ON u.unit_id = t.unit_id
             AND u.source_hash = t.source_hash
            WHERE t.origin = 'manual'
              AND t.provider = 'human'
              AND t.is_reviewed = 1
              AND t.quality_status IN ('valid', 'approved')
            ORDER BY t.unit_id, t.source_hash
            """
        ).fetchall()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            payload = {
                "unit_id": row["unit_id"],
                "source_hash": row["source_hash"],
                "source": row["source"],
                "source_xml": row["source_xml"],
                "target": row["target"],
                "target_xml": row["target_xml"],
                "target_attrs": json.loads(row["target_attrs_json"] or "{}"),
                "quality_status": row["quality_status"],
                "updated_at": row["updated_at"],
            }
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"Exported reviewed manual targets: {len(rows)}")
    print(f"Output: {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export reviewed manual translations to JSONL.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/manual_targets.jsonl"))
    args = parser.parse_args()

    return export_reviewed_targets(args.db, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
