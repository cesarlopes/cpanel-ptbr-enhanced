"""Report CloudLinux i18n translation coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cloudlinux_i18n_common import flatten, load_json, placeholder_mismatch


def is_untranslated(source_value: Any, target_value: Any) -> bool:
    return isinstance(source_value, str) and target_value == source_value


def write_markdown(path: Path, rows: list[dict[str, Any]], stats: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# CloudLinux i18n untranslated strings\n\n")
        for key, value in stats.items():
            handle.write(f"- {key}: {value}\n")
        handle.write("\n")
        for row in rows:
            handle.write(f"## {row['key']}\n\n")
            handle.write("```text\n")
            handle.write(row["source"].replace("\r\n", "\n"))
            handle.write("\n```\n\n")


def write_json(path: Path, rows: list[dict[str, Any]], stats: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump({"stats": stats, "items": rows}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def report(args: argparse.Namespace) -> int:
    source = load_json(args.source)
    target = load_json(args.target)
    source_flat = flatten(source)
    target_flat = flatten(target)

    untranslated: list[dict[str, Any]] = []
    placeholder_issues: list[dict[str, Any]] = []

    for key in sorted(source_flat):
        source_value = source_flat[key]
        target_value = target_flat.get(key)
        if is_untranslated(source_value, target_value):
            untranslated.append({"key": key, "source": source_value})
        if isinstance(source_value, str) and isinstance(target_value, str):
            missing_tokens, extra_tokens = placeholder_mismatch(source_value, target_value)
            if missing_tokens or extra_tokens:
                placeholder_issues.append(
                    {
                        "key": key,
                        "missing": sorted(missing_tokens),
                        "extra": sorted(extra_tokens),
                    }
                )

    source_strings = sum(1 for value in source_flat.values() if isinstance(value, str))
    stats = {
        "source_strings": source_strings,
        "target_keys": len(target_flat),
        "untranslated": len(untranslated),
        "translated_or_changed": source_strings - len(untranslated),
        "placeholder_issues": len(placeholder_issues),
    }

    print("CloudLinux i18n coverage")
    for key, value in stats.items():
        print(f"{key}: {value}")

    if args.markdown:
        write_markdown(args.markdown, untranslated, stats)
        print(f"Markdown report: {args.markdown}")
    if args.json:
        write_json(args.json, untranslated, stats)
        print(f"JSON report: {args.json}")

    if placeholder_issues:
        print("Placeholder issues:")
        for issue in placeholder_issues[: args.show]:
            print(f"  {issue['key']}: missing={issue['missing']} extra={issue['extra']}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Report CloudLinux i18n translation coverage.")
    parser.add_argument(
        "target",
        type=Path,
        default=Path("cloudlinux/i18n/i-pt_br_enhanced.json"),
        nargs="?",
        help="Custom locale JSON file.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("cloudlinux-i18n/base.en-en.json"),
        help="Modern en-en JSON source file.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("cache/cloudlinux_untranslated.md"),
        help="Markdown report path.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("cache/cloudlinux_untranslated.json"),
        help="JSON report path.",
    )
    parser.add_argument("--show", type=int, default=20, help="Number of placeholder issue examples to show.")
    return report(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
