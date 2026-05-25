"""Shared helpers for CloudLinux i18n JSON files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PLACEHOLDER_RE = re.compile(
    r"""
    %\([A-Za-z_][\w.-]*\)[sd] |
    \{\{[A-Za-z_][\w.-]*\}\} |
    \{[A-Za-z_][\w.-]*\}
    """,
    re.VERBOSE,
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def dump_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, path))
        else:
            result[path] = value
    return result


def placeholders(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return set(PLACEHOLDER_RE.findall(value))


def placeholder_mismatch(source: Any, target: Any) -> tuple[set[str], set[str]]:
    source_tokens = placeholders(source)
    target_tokens = placeholders(target)
    return source_tokens - target_tokens, target_tokens - source_tokens


def set_path(data: dict[str, Any], dotted_path: str, value: Any) -> None:
    keys = dotted_path.split(".")
    current = data
    for key in keys[:-1]:
        next_value = current.setdefault(key, {})
        if not isinstance(next_value, dict):
            raise ValueError(f"Cannot create nested key below non-object path: {dotted_path}")
        current = next_value
    existing = current.get(keys[-1])
    if isinstance(existing, dict) and not isinstance(value, dict):
        raise ValueError(f"Cannot replace object with scalar at path: {dotted_path}")
    current[keys[-1]] = value
