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


def translated_value(
    path: str,
    source_value: Any,
    legacy_flat: dict[str, Any],
    overrides_flat: dict[str, Any],
) -> tuple[Any, str]:
    if path in overrides_flat:
        override = overrides_flat[path]
        if not compatible(source_value, override):
            raise ValueError(f"Manual override has incompatible placeholders: {path}")
        return override, "manual-override"

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
    overrides = load_json(args.overrides) if args.overrides.exists() else {}
    extra_overrides = load_json(args.extra_overrides) if args.extra_overrides.exists() else {}
    source_flat = flatten(source)
    legacy_flat = flatten(legacy)
    overrides_flat = flatten(overrides)
    extra_overrides_flat = flatten(extra_overrides)

    unknown_overrides = sorted(set(overrides_flat) - set(source_flat))
    if unknown_overrides:
        raise ValueError(
            "Manual overrides contain keys not found in the source JSON: "
            + ", ".join(unknown_overrides[:10])
        )

    output: dict[str, Any] = {}
    stats = {
        "manual-override": 0,
        "manual-fix": 0,
        "legacy-path": 0,
        "legacy-source": 0,
        "source-fallback": 0,
        "legacy-extra": 0,
        "legacy-extra-conflict": 0,
        "extra-override": 0,
        "extra-override-conflict": 0,
    }

    for path, source_value in source_flat.items():
        value, origin = translated_value(path, source_value, legacy_flat, overrides_flat)
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

    for path, value in extra_overrides_flat.items():
        try:
            set_path(output, path, deepcopy(value))
        except ValueError:
            stats["extra-override-conflict"] += 1
            continue
        stats["extra-override"] += 1

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
        "--overrides",
        type=Path,
        default=Path("cloudlinux-i18n-ptbr/manual_overrides.json"),
        help="Manual pt-br overrides applied before legacy translation memory.",
    )
    parser.add_argument(
        "--extra-overrides",
        type=Path,
        default=Path("cloudlinux-i18n-ptbr/extra_overrides.json"),
        help="Extra literal i18n keys not present in the modern en-en JSON.",
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
