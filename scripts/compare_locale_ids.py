"""Compare trans-unit IDs between two XLF/XLIFF files.

This is intentionally narrower than compare_locales.py: it only compares IDs,
which makes it useful for large locale files when you want a quick structural
answer without printing every changed string.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def load_ids(path: Path) -> set[str]:
    ids: set[str] = set()

    for _event, element in ET.iterparse(path, events=("end",)):
        if local_name(element.tag) == "trans-unit":
            unit_id = element.attrib.get("id")
            if unit_id:
                ids.add(unit_id)
            element.clear()

    return ids


def write_list(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(values) + ("\n" if values else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare trans-unit IDs between two XLF files.")
    parser.add_argument("left", type=Path, help="First XLF file.")
    parser.add_argument("right", type=Path, help="Second XLF file.")
    parser.add_argument("--output-dir", type=Path, help="Optional directory to write ID lists.")
    args = parser.parse_args()

    left_ids = load_ids(args.left)
    right_ids = load_ids(args.right)

    only_left = sorted(left_ids - right_ids)
    only_right = sorted(right_ids - left_ids)
    common = sorted(left_ids & right_ids)

    print("ID comparison")
    print(f"Left: {args.left}")
    print(f"Right: {args.right}")
    print(f"Left IDs: {len(left_ids)}")
    print(f"Right IDs: {len(right_ids)}")
    print(f"Common IDs: {len(common)}")
    print(f"Only left: {len(only_left)}")
    print(f"Only right: {len(only_right)}")

    print("\nBy prefix")
    for prefix in ("tu-", "bn-"):
        print(
            f"{prefix} only left: {sum(item.startswith(prefix) for item in only_left)} | "
            f"only right: {sum(item.startswith(prefix) for item in only_right)}"
        )

    if args.output_dir:
        write_list(args.output_dir / "common_ids.txt", common)
        write_list(args.output_dir / "only_left_ids.txt", only_left)
        write_list(args.output_dir / "only_right_ids.txt", only_right)
        print(f"\nWrote ID lists to: {args.output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
