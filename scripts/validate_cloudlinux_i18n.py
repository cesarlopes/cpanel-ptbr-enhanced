"""Validate a CloudLinux i18n JSON file against an English source file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cloudlinux_i18n_common import flatten, load_json, placeholder_mismatch


def validate(args: argparse.Namespace) -> int:
    source = load_json(args.source)
    target = load_json(args.target)
    source_flat = flatten(source)
    target_flat = flatten(target)

    errors: list[str] = []
    warnings: list[str] = []

    missing = sorted(set(source_flat) - set(target_flat))
    if missing:
        errors.append(f"Missing modern source keys: {len(missing)}")
        errors.extend(f"  missing: {key}" for key in missing[: args.show])

    empty = [key for key, value in target_flat.items() if isinstance(value, str) and value == ""]
    if empty:
        errors.append(f"Empty string values: {len(empty)}")
        errors.extend(f"  empty: {key}" for key in empty[: args.show])

    for key in sorted(set(source_flat) & set(target_flat)):
        source_value = source_flat[key]
        target_value = target_flat[key]
        if not isinstance(source_value, str) or not isinstance(target_value, str):
            continue
        missing_tokens, extra_tokens = placeholder_mismatch(source_value, target_value)
        if missing_tokens or extra_tokens:
            errors.append(
                f"Placeholder mismatch at {key}: "
                f"missing={sorted(missing_tokens)} extra={sorted(extra_tokens)}"
            )

    extra_count = len(set(target_flat) - set(source_flat))
    if extra_count:
        warnings.append(f"Legacy/extra keys preserved: {extra_count}")

    print(f"Source keys: {len(source_flat)}")
    print(f"Target keys: {len(target_flat)}")
    for warning in warnings:
        print(f"WARNING: {warning}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("CloudLinux i18n validation passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a CloudLinux i18n JSON file.")
    parser.add_argument("target", type=Path, help="JSON file to validate.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("cloudlinux-i18n/base.en-en.json"),
        help="Modern en-en JSON source file.",
    )
    parser.add_argument("--show", type=int, default=20, help="Number of key examples to show.")
    return validate(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
