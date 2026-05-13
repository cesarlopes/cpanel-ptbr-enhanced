"""Translate XLF targets unit by unit with a pluggable AI provider.

This script is designed for the review/retranslation phase. It never edits the
source text. It translates each trans-unit target from the source text, preserves
inline XLF tags through temporary tokens, validates the result, and writes a new
XLF file.

Use --provider stub for a safe dry run. Use --provider openai only when
OPENAI_API_KEY is configured and you are ready to spend API credits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CP_NS = "tag:cpanel.net,2012-01:translate"
TOKEN_RE = re.compile(r"__XLF_TAG_(\d+)__")
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â€¦", "�")
PLACEHOLDER_RE = re.compile(
    r"""
    \[_\d+\]                 |
    %[sd]                    |
    \{\{[A-Za-z_][\w.-]*\}\} |
    \{[A-Za-z_][\w.-]*\}     |
    :[A-Za-z_][\w.-]*
    """,
    re.VERBOSE,
)


@dataclass
class TokenizedSource:
    text: str
    children: list[ET.Element]
    source_tags: list[str]


@dataclass
class TranslationResult:
    text: str
    cached: bool = False


class Translator(ABC):
    @abstractmethod
    def translate(self, source: str, *, unit_id: str, glossary: dict[str, Any], retry_note: str = "") -> str:
        """Translate one tokenized source string."""


class StubTranslator(Translator):
    def translate(self, source: str, *, unit_id: str, glossary: dict[str, Any], retry_note: str = "") -> str:
        return source


class OpenAITranslator(Translator):
    def __init__(self, model: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the OpenAI SDK first: pip install openai") from exc

        self.model = model
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env or export it in your shell."
            )
        self.client = OpenAI(api_key=api_key)

    def translate(self, source: str, *, unit_id: str, glossary: dict[str, Any], retry_note: str = "") -> str:
        preserve_terms = ", ".join(glossary.get("preserve", []))
        preferred_terms = json.dumps(glossary.get("translations", {}), ensure_ascii=False)
        fixed_phrases = json.dumps(glossary.get("phrases", {}), ensure_ascii=False)
        instructions = (
            "You are revising cPanel & WHM XLIFF UI localization from English to Brazilian Portuguese. "
            "Use Brazilian Portuguese suitable for a technical hosting control panel. "
            "Prefer clear UI language over literal translation. "
            "cPanel & WHM includes cron jobs, email accounts, DNS, SSL/TLS, FTP, SSH, databases, backups, packages, domains, and server administration. "
            "Short fragments may be labels, dropdown options, validation messages, or cron schedule descriptions. "
            "For schedule expressions, translate semantically: 'past the hour' means 'minutos de cada hora', not a clock time. "
            "'one quarter past the hour' means '15 minutes after each hour'. "
            "'one half past the hour' means '30 minutes after each hour'. "
            "'one quarter until the hour' means '45 minutes after each hour'. "
            "Return only the translated text, with no quotes and no explanation. "
            "Preserve every placeholder exactly, including tokens like [_1], %s, %d, {name}, {{name}}, and :name. "
            "Preserve every __XLF_TAG_N__ token exactly once and keep it in the most natural position. "
            "Do not translate product/protocol names when they are technical terms. "
            f"Terms to preserve: {preserve_terms}. "
            f"Preferred glossary translations as JSON: {preferred_terms}. "
            f"Mandatory fixed phrase translations as JSON: {fixed_phrases}."
        )
        if retry_note:
            instructions += " " + retry_note

        response = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=f"Unit ID: {unit_id}\nSource: {source}",
        )
        return response.output_text.strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_child(unit: ET.Element, child_name: str) -> ET.Element | None:
    for child in unit:
        if local_name(child.tag) == child_name:
            return child
    return None


def tag_signature(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    return [local_name(child.tag) for child in element.iter() if child is not element]


def text_content(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def tokenized_source(source: ET.Element) -> TokenizedSource:
    parts: list[str] = []
    children = list(source)

    if source.text:
        parts.append(source.text)

    for index, child in enumerate(children):
        parts.append(f"__XLF_TAG_{index}__")
        if child.tail:
            parts.append(child.tail)

    return TokenizedSource(
        text="".join(parts).strip(),
        children=children,
        source_tags=tag_signature(source),
    )


def build_target_from_translation(tokenized: TokenizedSource, translated: str) -> ET.Element:
    target = ET.Element(f"{{{XLIFF_NS}}}target")
    pieces = TOKEN_RE.split(translated)
    expected = list(range(len(tokenized.children)))
    found = [int(pieces[index]) for index in range(1, len(pieces), 2)]

    if found != expected:
        raise ValueError(f"inline token mismatch: expected {expected}, got {found}")

    target.text = pieces[0]
    for piece_index, child_index in enumerate(found):
        child = deepcopy(tokenized.children[child_index])
        child.tail = pieces[(piece_index * 2) + 2]
        target.append(child)

    return target


def expected_token_note(tokenized: TokenizedSource) -> str:
    tokens = " ".join(f"__XLF_TAG_{index}__" for index in range(len(tokenized.children)))
    return (
        "Your previous answer did not preserve all inline tokens. "
        f"You must include these tokens exactly once and in this order: {tokens}. "
        "Do not omit, rename, duplicate, or translate these tokens."
    )


def replace_or_insert_target(unit: ET.Element, new_target: ET.Element) -> None:
    old_target = find_child(unit, "target")
    if old_target is not None:
        children = list(unit)
        index = children.index(old_target)
        unit.remove(old_target)
        unit.insert(index, new_target)
        return

    source = find_child(unit, "source")
    if source is None:
        unit.append(new_target)
        return

    children = list(unit)
    unit.insert(children.index(source) + 1, new_target)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_glossary(terms_path: Path, phrases_path: Path) -> dict[str, Any]:
    glossary = load_json(terms_path)
    glossary["phrases"] = load_json(phrases_path)
    return glossary


def fixed_phrase_translation(source: str, glossary: dict[str, Any]) -> str | None:
    phrases = glossary.get("phrases", {})
    return phrases.get(source)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def source_hash(model: str, source: str, glossary: dict[str, Any]) -> str:
    payload = json.dumps(
        {"model": model, "source": source, "glossary": glossary},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cache(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}

    cache: dict[tuple[str, str], str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        cache[(item["id"], item["source_hash"])] = item["translation"]
    return cache


def append_cache(path: Path, unit_id: str, hash_value: str, translation: str, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "id": unit_id,
        "source_hash": hash_value,
        "model": model,
        "translation": translation,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def checkpoint_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".partial")


def write_tree(path: Path, tree: ET.ElementTree) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def should_translate(unit: ET.Element, mode: str) -> bool:
    source = find_child(unit, "source")
    target = find_child(unit, "target")

    if source is None:
        return False
    if mode == "all":
        return True
    if target is None:
        return True
    if target.attrib.get("state") == "needs-translation":
        return True
    return False


def validate_target(source: ET.Element, target: ET.Element) -> None:
    source_text = text_content(source)
    target_text = text_content(target)
    if any(marker in target_text for marker in MOJIBAKE_MARKERS):
        raise ValueError("target appears to contain mojibake/encoding artifacts")

    missing = placeholders(source_text) - placeholders(target_text)
    if missing:
        raise ValueError("missing placeholders: " + ", ".join(sorted(missing)))

    if tag_signature(source) != tag_signature(target):
        raise ValueError("inline tag signature mismatch")


def translate_locale(args: argparse.Namespace) -> int:
    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)

    glossary = load_glossary(args.glossary, args.phrases)
    cache = load_cache(args.cache)
    translator: Translator

    if args.provider == "openai":
        translator = OpenAITranslator(args.model)
    else:
        translator = StubTranslator()

    tree = ET.parse(args.input)
    total_selected = sum(
        1
        for unit in tree.getroot().iter()
        if local_name(unit.tag) == "trans-unit" and should_translate(unit, args.mode)
    )
    processed = 0
    translated = 0
    cached = 0
    failed: list[dict[str, str]] = []

    if args.limit is not None:
        total_display = min(total_selected, args.limit)
    else:
        total_display = total_selected

    if args.progress:
        print(
            f"Starting translation: input={args.input} mode={args.mode} provider={args.provider} "
            f"selected={total_selected} limit={args.limit or 'none'}",
            flush=True,
        )

    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        if args.limit is not None and processed >= args.limit:
            break
        if not should_translate(unit, args.mode):
            continue

        unit_id = unit.attrib.get("id", "<missing-id>")
        source = find_child(unit, "source")
        if source is None:
            continue

        tokenized = tokenized_source(source)
        hash_value = source_hash(args.model, tokenized.text, glossary)
        processed += 1
        progress_prefix = f"[{processed}/{total_display}] {unit_id}"

        try:
            cache_key = (unit_id, hash_value)
            if cache_key in cache:
                translated_text = cache[cache_key]
                cached += 1
                origin = "cache"
            elif fixed := fixed_phrase_translation(tokenized.text, glossary):
                translated_text = fixed
                origin = "fixed"
            elif args.dry_run:
                translated_text = tokenized.text
                origin = "dry-run"
            else:
                origin = args.provider
                if args.progress:
                    print(f"{progress_prefix} sending", flush=True)
                translated_text = translator.translate(tokenized.text, unit_id=unit_id, glossary=glossary)
                for _attempt in range(args.retries):
                    try:
                        build_target_from_translation(tokenized, translated_text)
                        break
                    except ValueError:
                        if args.progress:
                            print(f"{progress_prefix} retrying token preservation", flush=True)
                        translated_text = translator.translate(
                            tokenized.text,
                            unit_id=unit_id,
                            glossary=glossary,
                            retry_note=expected_token_note(tokenized),
                        )

            target = build_target_from_translation(tokenized, translated_text)
            validate_target(source, target)
            if cache_key not in cache and not args.dry_run:
                append_cache(args.cache, unit_id, hash_value, translated_text, args.model)
            target.set("state", "translated" if args.provider != "stub" and not args.dry_run else "needs-review")
            target.set(f"{{{CP_NS}}}translated-by", args.provider)
            replace_or_insert_target(unit, target)
            translated += 1

            if args.progress:
                print(
                    f"{progress_prefix} ok origin={origin} translated={translated} "
                    f"cache_hits={cached} failed={len(failed)}",
                    flush=True,
                )

            if args.sleep:
                time.sleep(args.sleep)
        except Exception as exc:  # noqa: BLE001 - keep processing other units.
            failed.append({"id": unit_id, "error": str(exc)})
            if args.progress:
                print(f"{progress_prefix} failed: {exc}", file=sys.stderr, flush=True)

        if (
            not args.dry_run
            and args.checkpoint_every
            and processed % args.checkpoint_every == 0
        ):
            partial_path = checkpoint_path(args.output)
            write_tree(partial_path, tree)
            if args.progress:
                print(f"Checkpoint written: {partial_path}", flush=True)

    if not args.dry_run:
        write_tree(args.output, tree)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "input": str(args.input),
                    "output": str(args.output),
                    "provider": args.provider,
                    "model": args.model,
                    "mode": args.mode,
                    "dry_run": args.dry_run,
                    "processed": processed,
                    "translated": translated,
                    "cached": cached,
                    "failed": failed,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"Processed: {processed}")
    print(f"Translated/prepared: {translated}")
    print(f"Cache hits: {cached}")
    print(f"Failed: {len(failed)}")
    if args.dry_run:
        print("Dry run only: no output file was written.")
    else:
        print(f"Output: {args.output}")
    if args.report:
        print(f"Report: {args.report}")
    return 0 if not failed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate XLF units with a stub or OpenAI provider.")
    parser.add_argument("--input", type=Path, default=Path("output/pt_BR.xlf"))
    parser.add_argument("--output", type=Path, default=Path("output/pt_BR.ai.xlf"))
    parser.add_argument("--glossary", type=Path, default=Path("glossary/pt_BR_terms.json"))
    parser.add_argument("--phrases", type=Path, default=Path("glossary/pt_BR_phrases.json"))
    parser.add_argument("--cache", type=Path, default=Path("cache/ai_translations.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("cache/ai_translate_report.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--provider", choices=("stub", "openai"), default="stub")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--mode", choices=("pending", "all"), default="pending")
    parser.add_argument("--limit", type=int, help="Translate at most N units.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to wait between API calls.")
    parser.add_argument("--retries", type=int, default=1, help="Retry failed token preservation this many times.")
    parser.add_argument("--checkpoint-every", type=int, help="Write output.xlf.partial every N processed units.")
    parser.add_argument("--no-progress", dest="progress", action="store_false", help="Disable per-unit progress output.")
    parser.set_defaults(progress=True)
    parser.add_argument("--dry-run", action="store_true", help="Validate selection without writing output or calling APIs.")
    args = parser.parse_args()

    load_env_file(args.env_file)
    return translate_locale(args)


if __name__ == "__main__":
    raise SystemExit(main())
