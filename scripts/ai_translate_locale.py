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


@dataclass
class TimingStats:
    startup: float = 0.0
    load_cache: float = 0.0
    load_xml: float = 0.0
    count_units: float = 0.0
    api: float = 0.0
    cache_lookup: float = 0.0
    build_validate: float = 0.0
    checkpoint: float = 0.0
    write_output: float = 0.0
    report: float = 0.0


class Translator(ABC):
    @abstractmethod
    def translate(self, source: str, *, unit_id: str, glossary: dict[str, Any], retry_note: str = "") -> str:
        """Translate one tokenized source string."""


class StubTranslator(Translator):
    def translate(self, source: str, *, unit_id: str, glossary: dict[str, Any], retry_note: str = "") -> str:
        return source


class OpenAITranslator(Translator):
    def __init__(self, model: str, provider: str = "openai") -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install the OpenAI SDK first: pip install openai") from exc

        self.model = model
        self.provider = provider
        if provider == "xai":
            api_key = os.environ.get("XAI_API_KEY")
            base_url = "https://api.x.ai/v1"
            env_name = "XAI_API_KEY"
        else:
            api_key = os.environ.get("OPENAI_API_KEY")
            base_url = None
            env_name = "OPENAI_API_KEY"

        if not api_key:
            raise RuntimeError(
                f"{env_name} is not set. Add it to .env or export it in your shell."
            )
        if base_url:
            self.client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            self.client = OpenAI(api_key=api_key)

    def translate(self, source: str, *, unit_id: str, glossary: dict[str, Any], retry_note: str = "") -> str:
        preserve_terms = ", ".join(glossary.get("preserve", []))
        preferred_terms = json.dumps(glossary.get("translations", {}), ensure_ascii=False)
        fixed_phrases = json.dumps(glossary.get("phrases", {}), ensure_ascii=False)
        instructions = (
            "You are revising cPanel & WHM XLIFF UI localization from English to Brazilian Portuguese. "
            "Use Brazilian Portuguese suitable for a technical hosting control panel. "
            "Use correct Brazilian Portuguese spelling with accents and diacritics; do not omit accents in words such as domínio, domínios, usuário, usuários, configuração, configurações, opção, opções, informação, informações, ação, ações, permissão, permissões, autenticação, não. "
            "Prefer natural Brazilian Portuguese UI wording; avoid awkward literal phrasing. "
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
        "Do not omit, rename, duplicate, reorder, or translate these tokens. "
        "Keep the tokens in numeric order even if Portuguese word order would otherwise change."
    )


def quality_retry_note(error: Exception, tokenized: TokenizedSource) -> str:
    message = str(error)
    if "inline token mismatch" in message:
        return expected_token_note(tokenized)
    if "mojibake" in message:
        return (
            "Your previous answer contained mojibake or encoding artifacts such as Ã, Â, â€, or �. "
            "Return proper UTF-8 Brazilian Portuguese characters, for example: ação, usuário, domínio, não."
        )
    return (
        "Your previous answer failed validation. Preserve all placeholders and inline tokens exactly, "
        "and return only valid Brazilian Portuguese text."
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


def elapsed_since(start: float) -> float:
    return time.perf_counter() - start


def format_seconds(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.2f}s"


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


def load_fallback_caches(paths: list[Path], model: str) -> list[tuple[Path, str, dict[tuple[str, str], str]]]:
    return [(path, model, load_cache(path)) for path in paths if path.exists()]


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
    run_started = time.perf_counter()
    stats = TimingStats()

    startup_started = time.perf_counter()
    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)

    glossary = load_glossary(args.glossary, args.phrases)
    stats.startup += elapsed_since(startup_started)

    cache_started = time.perf_counter()
    cache = load_cache(args.cache)
    fallback_caches = load_fallback_caches(args.fallback_cache, args.fallback_model)
    stats.load_cache += elapsed_since(cache_started)
    translator: Translator

    if args.provider in ("openai", "xai"):
        translator = OpenAITranslator(args.model, args.provider)
    else:
        translator = StubTranslator()

    xml_started = time.perf_counter()
    tree = ET.parse(args.input)
    stats.load_xml += elapsed_since(xml_started)

    count_started = time.perf_counter()
    total_selected = sum(
        1
        for unit in tree.getroot().iter()
        if local_name(unit.tag) == "trans-unit" and should_translate(unit, args.mode)
    )
    stats.count_units += elapsed_since(count_started)
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
        if fallback_caches:
            print(
                "Fallback caches: "
                + ", ".join(f"{path} as {model}" for path, model, _cache in fallback_caches),
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
            unit_started = time.perf_counter()
            api_elapsed = 0.0
            build_elapsed = 0.0

            lookup_started = time.perf_counter()
            cache_key = (unit_id, hash_value)
            if cache_key in cache:
                translated_text = cache[cache_key]
                cached += 1
                origin = "cache"
            elif fallback_hit := next(
                (
                    fallback_cache[(unit_id, source_hash(fallback_model, tokenized.text, glossary))]
                    for _fallback_path, fallback_model, fallback_cache in fallback_caches
                    if (unit_id, source_hash(fallback_model, tokenized.text, glossary)) in fallback_cache
                ),
                None,
            ):
                translated_text = fallback_hit
                cached += 1
                origin = "fallback-cache"
            elif fixed := fixed_phrase_translation(tokenized.text, glossary):
                translated_text = fixed
                origin = "fixed"
            elif args.dry_run:
                translated_text = tokenized.text
                origin = "dry-run"
            else:
                stats.cache_lookup += elapsed_since(lookup_started)
                lookup_started = None
                origin = args.provider
                if args.progress:
                    print(f"{progress_prefix} sending", flush=True)
                api_started = time.perf_counter()
                translated_text = translator.translate(tokenized.text, unit_id=unit_id, glossary=glossary)
                api_elapsed += elapsed_since(api_started)
                for _attempt in range(args.retries):
                    try:
                        build_started = time.perf_counter()
                        retry_target = build_target_from_translation(tokenized, translated_text)
                        validate_target(source, retry_target)
                        build_elapsed += elapsed_since(build_started)
                        break
                    except ValueError as retry_error:
                        if args.progress:
                            print(f"{progress_prefix} retrying after validation error: {retry_error}", flush=True)
                        api_started = time.perf_counter()
                        translated_text = translator.translate(
                            tokenized.text,
                            unit_id=unit_id,
                            glossary=glossary,
                            retry_note=quality_retry_note(retry_error, tokenized),
                        )
                        api_elapsed += elapsed_since(api_started)
                stats.api += api_elapsed
            if lookup_started is not None:
                stats.cache_lookup += elapsed_since(lookup_started)

            build_started = time.perf_counter()
            target = build_target_from_translation(tokenized, translated_text)
            validate_target(source, target)
            build_elapsed += elapsed_since(build_started)
            stats.build_validate += build_elapsed
            if cache_key not in cache and not args.dry_run:
                append_cache(args.cache, unit_id, hash_value, translated_text, args.model)
            target.set("state", "translated" if args.provider != "stub" and not args.dry_run else "needs-review")
            target.set(f"{{{CP_NS}}}translated-by", args.provider)
            replace_or_insert_target(unit, target)
            translated += 1

            if args.progress:
                unit_elapsed = elapsed_since(unit_started)
                print(
                    f"{progress_prefix} ok origin={origin} translated={translated} "
                    f"cache_hits={cached} failed={len(failed)} "
                    f"api={format_seconds(api_elapsed) if api_elapsed else '-'} "
                    f"build={format_seconds(build_elapsed)} unit={format_seconds(unit_elapsed)} "
                    f"total={format_seconds(elapsed_since(run_started))}",
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
            checkpoint_started = time.perf_counter()
            write_tree(partial_path, tree)
            checkpoint_elapsed = elapsed_since(checkpoint_started)
            stats.checkpoint += checkpoint_elapsed
            if args.progress:
                print(f"Checkpoint written: {partial_path} in {format_seconds(checkpoint_elapsed)}", flush=True)

    if not args.dry_run:
        write_started = time.perf_counter()
        write_tree(args.output, tree)
        stats.write_output += elapsed_since(write_started)

    if args.report:
        report_started = time.perf_counter()
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
                    "timings_seconds": {
                        "startup": stats.startup,
                        "load_cache": stats.load_cache,
                        "load_xml": stats.load_xml,
                        "count_units": stats.count_units,
                        "api": stats.api,
                        "cache_lookup": stats.cache_lookup,
                        "build_validate": stats.build_validate,
                        "checkpoint": stats.checkpoint,
                        "write_output": stats.write_output,
                        "report": stats.report,
                        "total": elapsed_since(run_started),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        stats.report += elapsed_since(report_started)

    print(f"Processed: {processed}")
    print(f"Translated/prepared: {translated}")
    print(f"Cache hits: {cached}")
    print(f"Failed: {len(failed)}")
    print("Timings:")
    print(f"- startup: {format_seconds(stats.startup)}")
    print(f"- load cache: {format_seconds(stats.load_cache)}")
    print(f"- load XML: {format_seconds(stats.load_xml)}")
    print(f"- count units: {format_seconds(stats.count_units)}")
    print(f"- API calls: {format_seconds(stats.api)}")
    print(f"- cache lookup: {format_seconds(stats.cache_lookup)}")
    print(f"- build/validate: {format_seconds(stats.build_validate)}")
    print(f"- checkpoint writes: {format_seconds(stats.checkpoint)}")
    print(f"- final write: {format_seconds(stats.write_output)}")
    print(f"- report write: {format_seconds(stats.report)}")
    print(f"- total: {format_seconds(elapsed_since(run_started))}")
    if args.dry_run:
        print("Dry run only: no output file was written.")
    else:
        print(f"Output: {args.output}")
    if args.report:
        print(f"Report: {args.report}")
    return 0 if not failed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate XLF units with a stub, OpenAI, or xAI provider.")
    parser.add_argument("--input", type=Path, default=Path("output/pt_BR.xlf"))
    parser.add_argument("--output", type=Path, default=Path("output/pt_BR.ai.xlf"))
    parser.add_argument("--glossary", type=Path, default=Path("glossary/pt_BR_terms.json"))
    parser.add_argument("--phrases", type=Path, default=Path("glossary/pt_BR_phrases.json"))
    parser.add_argument("--cache", type=Path, default=Path("cache/ai_translations.jsonl"))
    parser.add_argument(
        "--fallback-cache",
        type=Path,
        nargs="*",
        default=[],
        help="Optional cache files to reuse before calling the selected model.",
    )
    parser.add_argument(
        "--fallback-model",
        default="gpt-5.4-mini",
        help="Model name originally used to create entries in --fallback-cache.",
    )
    parser.add_argument("--report", type=Path, default=Path("cache/ai_translate_report.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--provider", choices=("stub", "openai", "xai"), default="stub")
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
