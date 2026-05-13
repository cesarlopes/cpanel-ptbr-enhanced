"""Compare two XLF/XLIFF files by translation unit id.

The script reports units that are new, removed, or changed in the newer file.
It intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def local_name(tag: str) -> str:
    """Return the XML local name without namespace."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def inner_xml(element: ET.Element | None) -> str:
    """Serialize an element's inner XML, including inline XLF tags."""
    if element is None:
        return ""

    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in list(element):
        parts.append(ET.tostring(child, encoding="unicode"))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def find_child(unit: ET.Element, child_name: str) -> ET.Element | None:
    for child in unit:
        if local_name(child.tag) == child_name:
            return child
    return None


def load_units(path: Path) -> dict[str, dict[str, str]]:
    tree = ET.parse(path)
    units: dict[str, dict[str, str]] = {}

    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue

        unit_id = unit.attrib.get("id")
        if not unit_id:
            continue

        source = inner_xml(find_child(unit, "source"))
        target = inner_xml(find_child(unit, "target"))
        units[unit_id] = {"source": source, "target": target}

    return units


def compare_units(old_units: dict[str, dict[str, str]], new_units: dict[str, dict[str, str]]) -> dict[str, Any]:
    old_ids = set(old_units)
    new_ids = set(new_units)

    added = [
        {"id": unit_id, "source": new_units[unit_id]["source"]}
        for unit_id in sorted(new_ids - old_ids)
    ]
    removed = [
        {"id": unit_id, "source": old_units[unit_id]["source"]}
        for unit_id in sorted(old_ids - new_ids)
    ]
    changed = [
        {
            "id": unit_id,
            "old_source": old_units[unit_id]["source"],
            "new_source": new_units[unit_id]["source"],
        }
        for unit_id in sorted(old_ids & new_ids)
        if old_units[unit_id]["source"] != new_units[unit_id]["source"]
    ]

    return {
        "summary": {
            "old_total": len(old_units),
            "new_total": len(new_units),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def print_report(diff: dict[str, Any]) -> None:
    summary = diff["summary"]
    print("XLF comparison")
    print(f"Old units: {summary['old_total']}")
    print(f"New units: {summary['new_total']}")
    print(f"Added: {summary['added']}")
    print(f"Removed: {summary['removed']}")
    print(f"Changed: {summary['changed']}")

    for section in ("added", "removed", "changed"):
        if not diff[section]:
            continue
        print(f"\n{section.upper()}")
        for item in diff[section]:
            print(f"- {item['id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two XLF/XLIFF files by trans-unit id.")
    parser.add_argument("old_file", type=Path, help="Previous original XLF file.")
    parser.add_argument("new_file", type=Path, help="New original XLF file.")
    parser.add_argument("--json", type=Path, help="Optional path to write a JSON diff.")
    args = parser.parse_args()

    old_units = load_units(args.old_file)
    new_units = load_units(args.new_file)
    diff = compare_units(old_units, new_units)

    print_report(diff)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON diff written to: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
