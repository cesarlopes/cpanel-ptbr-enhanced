"""Export locale targets from SQLite to JSONL, optionally compressed.

This is useful for full backups or moving AI/cPanel/manual targets between
local databases. For Git, prefer `export_reviewed_targets.py`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from pathlib import Path
from typing import TextIO


def open_text_writer(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8", newline="\n")
    return path.open("w", encoding="utf-8", newline="\n")


def export_targets(db_path: Path, output_path: Path, origin: str, valid_only: bool) -> int:
    where = []
    params: dict[str, object] = {}
    if origin != "all":
        where.append("t.origin = :origin")
        params["origin"] = origin
    if valid_only:
        where.append("t.quality_status IN ('valid', 'approved')")

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                t.unit_id,
                t.source_hash,
                u.source,
                u.source_xml,
                t.target,
                t.target_xml,
                t.target_attrs_json,
                t.provider,
                t.model,
                t.origin,
                t.quality_status,
                t.is_reviewed,
                t.source_file,
                t.created_at,
                t.updated_at
            FROM locale_targets t
            JOIN locale_units u
              ON u.unit_id = t.unit_id
             AND u.source_hash = t.source_hash
            {where_sql}
            ORDER BY t.origin, t.provider, t.model, t.unit_id, t.source_hash, t.target_id
            """,
            params,
        ).fetchall()

    with open_text_writer(output_path) as handle:
        for row in rows:
            payload = {
                "unit_id": row["unit_id"],
                "source_hash": row["source_hash"],
                "source": row["source"],
                "source_xml": row["source_xml"],
                "target": row["target"],
                "target_xml": row["target_xml"],
                "target_attrs": json.loads(row["target_attrs_json"] or "{}"),
                "provider": row["provider"],
                "model": row["model"],
                "origin": row["origin"],
                "quality_status": row["quality_status"],
                "is_reviewed": bool(row["is_reviewed"]),
                "source_file": row["source_file"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    print(f"Exported locale targets: {len(rows)}")
    print(f"Origin: {origin}")
    print(f"Valid only: {valid_only}")
    print(f"Output: {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export locale_targets to JSONL or JSONL.GZ.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--output", type=Path, default=Path("data/locale_targets_snapshot.jsonl.gz"))
    parser.add_argument("--origin", choices=("all", "manual", "ai_cache", "cpanel"), default="all")
    parser.add_argument("--include-invalid", action="store_true", help="Also export invalid/source_equal/tag_mismatch targets.")
    args = parser.parse_args()

    return export_targets(args.db, args.output, args.origin, valid_only=not args.include_invalid)


if __name__ == "__main__":
    raise SystemExit(main())
