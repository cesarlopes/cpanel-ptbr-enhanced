"""Build the final locale file for WHM import."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET


def validate_xml(path: Path) -> None:
    ET.parse(path)


def build_locale(input_path: Path, output_path: Path) -> int:
    validate_xml(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, output_path)
    print(f"Built locale: {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a final XLF locale file in output/.")
    parser.add_argument("input_xlf", type=Path, help="Validated custom XLF file.")
    parser.add_argument("--output", type=Path, default=Path("output/pt_BR.xlf"), help="Final output file.")
    args = parser.parse_args()

    try:
        return build_locale(args.input_xlf, args.output)
    except ET.ParseError as exc:
        print(f"Build failed: invalid XML: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
