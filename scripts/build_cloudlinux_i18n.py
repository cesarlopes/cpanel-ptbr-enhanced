"""Build the custom CloudLinux i18n JSON for i_pt_br_enhanced."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Any

from cloudlinux_i18n_common import dump_json, flatten, load_json, placeholder_mismatch, set_path


MANUAL_FIXES = {
    "%(days)s days": "%(days)s dias",
    "Can't run %(command)s": "Não é possível executar %(command)s",
    "noVersionsEnabled": "Não é possível criar um aplicativo. Nenhuma versão {{interpreter}} está disponível. Entre em contato com o administrador.",
}


def compatible(source_value: Any, target_value: Any) -> bool:
    if not isinstance(source_value, str) or not isinstance(target_value, str):
        return True
    missing_tokens, extra_tokens = placeholder_mismatch(source_value, target_value)
    return not missing_tokens and not extra_tokens


def translated_value(path: str, source_value: Any, legacy_flat: dict[str, Any]) -> tuple[Any, str]:
    if path in MANUAL_FIXES:
        fixed = MANUAL_FIXES[path]
        if compatible(source_value, fixed):
            return fixed, "manual-fix"

    if path in legacy_flat and compatible(source_value, legacy_flat[path]):
        return legacy_flat[path], "legacy-path"

    if isinstance(source_value, str) and source_value in legacy_flat and compatible(source_value, legacy_flat[source_value]):
        return legacy_flat[source_value], "legacy-source"

    return source_value, "source-fallback"


def build(args: argparse.Namespace) -> int:
    source = load_json(args.source)
    legacy = load_json(args.legacy)
    source_flat = flatten(source)
    legacy_flat = flatten(legacy)

    output: dict[str, Any] = {}
    stats = {
        "manual-fix": 0,
        "legacy-path": 0,
        "legacy-source": 0,
        "source-fallback": 0,
        "legacy-extra": 0,
        "legacy-extra-conflict": 0,
    }

    for path, source_value in source_flat.items():
        value, origin = translated_value(path, source_value, legacy_flat)
        set_path(output, path, deepcopy(value))
        stats[origin] += 1

    if args.preserve_legacy_extras:
        for path, value in legacy_flat.items():
            if path in source_flat:
                continue
            try:
                set_path(output, path, deepcopy(value))
            except ValueError:
                stats["legacy-extra-conflict"] += 1
                continue
            stats["legacy-extra"] += 1

    dump_json(args.output, output)
    print(f"Built CloudLinux i18n JSON: {args.output}")
    for alias_output in args.alias_output:
        dump_json(alias_output, output)
        print(f"Built CloudLinux i18n alias JSON: {alias_output}")
    for name, count in stats.items():
        print(f"{name}: {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build i-pt_br_enhanced CloudLinux i18n JSON.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("cloudlinux-i18n/base.en-en.json"),
        help="Modern en-en JSON source file.",
    )
    parser.add_argument(
        "--legacy",
        type=Path,
        default=Path("cloudlinux-i18n-ptbr/base.pt-br.json"),
        help="Legacy pt-br JSON translation memory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("cloudlinux/i18n/i-pt_br_enhanced.json"),
        help="Custom locale JSON output path.",
    )
    parser.add_argument(
        "--alias-output",
        type=Path,
        action="append",
        default=[Path("cloudlinux/i18n/i-pt-br-enhanced.json")],
        help="Additional normalized locale JSON output path. Can be repeated.",
    )
    parser.add_argument(
        "--preserve-legacy-extras",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Preserve keys found only in the legacy pt-br JSON.",
    )
    return build(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
