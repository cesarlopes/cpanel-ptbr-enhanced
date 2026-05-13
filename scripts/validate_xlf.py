"""Validate basic XLF/XLIFF structure and placeholder consistency."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


PLACEHOLDER_RE = re.compile(
    r"""
    \[_\d+\]            | # cPanel-style placeholders: [_1]
    %[sd]               | # printf placeholders: %s, %d
    \{\{[A-Za-z_][\w.-]*\}\} | # double-brace placeholders: {{name}}
    \{[A-Za-z_][\w.-]*\}     | # brace placeholders: {name}
    :[A-Za-z_][\w.-]*        # colon placeholders: :name
    """,
    re.VERBOSE,
)


@dataclass
class ValidationIssue:
    unit_id: str
    message: str


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_child(unit: ET.Element, child_name: str) -> ET.Element | None:
    for child in unit:
        if local_name(child.tag) == child_name:
            return child
    return None


def text_content(element: ET.Element | None) -> str:
    return "".join(element.itertext()) if element is not None else ""


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def inline_tag_signature(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    return [local_name(child.tag) for child in element.iter() if child is not element]


def validate_file(path: Path) -> list[ValidationIssue]:
    tree = ET.parse(path)
    issues: list[ValidationIssue] = []

    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue

        unit_id = unit.attrib.get("id", "<missing-id>")
        source = find_child(unit, "source")
        target = find_child(unit, "target")

        source_text = text_content(source)
        target_text = text_content(target)

        missing_placeholders = placeholders(source_text) - placeholders(target_text)
        if missing_placeholders:
            issues.append(
                ValidationIssue(
                    unit_id,
                    "missing placeholders in target: " + ", ".join(sorted(missing_placeholders)),
                )
            )

        source_tags = inline_tag_signature(source)
        target_tags = inline_tag_signature(target)
        if source_tags != target_tags:
            issues.append(
                ValidationIssue(
                    unit_id,
                    f"inline tag mismatch: source={source_tags} target={target_tags}",
                )
            )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate XLF/XML and placeholder consistency.")
    parser.add_argument("xlf_file", type=Path, help="XLF/XLIFF file to validate.")
    args = parser.parse_args()

    try:
        issues = validate_file(args.xlf_file)
    except ET.ParseError as exc:
        print(f"Invalid XML: {exc}")
        return 1

    if not issues:
        print(f"OK: {args.xlf_file} is well formed and passed basic checks.")
        return 0

    print(f"Validation failed: {len(issues)} issue(s) found.")
    for issue in issues:
        print(f"- {issue.unit_id}: {issue.message}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
