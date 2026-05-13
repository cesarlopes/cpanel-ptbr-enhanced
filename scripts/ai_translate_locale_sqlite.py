"""Translate XLF targets with SQLite cache and parallel OpenAI requests.

This is a parallel variant of ai_translate_locale.py. It keeps the same safety
ideas: translate per trans-unit, preserve inline XLF tokens, validate before
writing, and checkpoint periodically. The cache is stored in SQLite so multiple
workers can safely reuse/write translations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CP_NS = "tag:cpanel.net,2012-01:translate"
TOKEN_RE = re.compile(r"__XLF_TAG_(\d+)__")
MOJIBAKE_MARKERS = ("Ãƒ", "Ã‚", "Ã¢â‚¬", "Ã¢â‚¬Â¦", "ï¿½")
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


@dataclass
class WorkItem:
    index: int
    unit_id: str
    unit: ET.Element
    source: ET.Element
    tokenized: TokenizedSource
    source_hash: str


@dataclass
class WorkResult:
    item: WorkItem
    translation: str | None
    origin: str
    ok: bool
    error: str = ""
    api_seconds: float = 0.0
    attempts: int = 0


class SQLiteCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    source TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id, source_hash, model)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS translation_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.conn.commit()

    def get(self, unit_id: str, source_hash_value: str, model: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT translation
                FROM translations
                WHERE id = ? AND source_hash = ? AND model = ? AND status = 'ok'
                """,
                (unit_id, source_hash_value, model),
            ).fetchone()
        return row[0] if row else None

    def put_ok(self, unit_id: str, source_hash_value: str, model: str, source: str, translation: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO translations (id, source_hash, model, source, translation, status, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ok', CURRENT_TIMESTAMP)
                ON CONFLICT(id, source_hash, model) DO UPDATE SET
                    translation = excluded.translation,
                    status = 'ok',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (unit_id, source_hash_value, model, source, translation),
            )
            self.conn.commit()

    def put_attempt(
        self,
        unit_id: str,
        source_hash_value: str,
        model: str,
        status: str,
        error: str,
        duration_ms: int,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO translation_attempts (id, source_hash, model, status, error, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (unit_id, source_hash_value, model, status, error, duration_ms),
            )
            self.conn.commit()


class OpenAITranslator:
    _local = threading.local()

    def __init__(self, model: str) -> None:
        self.model = model
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to .env or export it in your shell.")

    def client(self) -> Any:
        if not hasattr(self._local, "client"):
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Install the OpenAI SDK first: pip install openai") from exc
            self._local.client = OpenAI(api_key=self.api_key)
        return self._local.client

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

        response = self.client().responses.create(
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
    return TokenizedSource(text="".join(parts).strip(), children=children)


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
    unit.insert(list(unit).index(source) + 1, new_target)


def should_translate(unit: ET.Element, mode: str) -> bool:
    source = find_child(unit, "source")
    target = find_child(unit, "target")
    if source is None:
        return False
    if mode == "all":
        return True
    if target is None:
        return True
    return target.attrib.get("state") == "needs-translation"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_glossary(terms_path: Path, phrases_path: Path) -> dict[str, Any]:
    glossary = load_json(terms_path)
    glossary["phrases"] = load_json(phrases_path)
    return glossary


def fixed_phrase_translation(source: str, glossary: dict[str, Any]) -> str | None:
    return glossary.get("phrases", {}).get(source)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def source_hash(model: str, source: str, glossary: dict[str, Any]) -> str:
    payload = json.dumps(
        {"model": model, "source": source, "glossary": glossary},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    return "Your previous answer failed validation. Preserve all placeholders and inline tokens exactly."


def checkpoint_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".partial")


def write_tree(path: Path, tree: ET.ElementTree) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def format_seconds(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.2f}s"


def collect_work_items(tree: ET.ElementTree, args: argparse.Namespace, glossary: dict[str, Any]) -> list[WorkItem]:
    items: list[WorkItem] = []
    for unit in tree.getroot().iter():
        if local_name(unit.tag) != "trans-unit":
            continue
        if args.limit is not None and len(items) >= args.limit:
            break
        if not should_translate(unit, args.mode):
            continue
        source = find_child(unit, "source")
        if source is None:
            continue
        unit_id = unit.attrib.get("id", "<missing-id>")
        tokenized = tokenized_source(source)
        items.append(
            WorkItem(
                index=len(items) + 1,
                unit_id=unit_id,
                unit=unit,
                source=source,
                tokenized=tokenized,
                source_hash=source_hash(args.model, tokenized.text, glossary),
            )
        )
    return items


def translate_one(
    item: WorkItem,
    translator: OpenAITranslator,
    glossary: dict[str, Any],
    model: str,
    retries: int,
) -> WorkResult:
    started = time.perf_counter()
    attempts = 0
    try:
        translated_text = translator.translate(item.tokenized.text, unit_id=item.unit_id, glossary=glossary)
        attempts += 1
        for _attempt in range(retries):
            try:
                target = build_target_from_translation(item.tokenized, translated_text)
                validate_target(item.source, target)
                break
            except ValueError as retry_error:
                translated_text = translator.translate(
                    item.tokenized.text,
                    unit_id=item.unit_id,
                    glossary=glossary,
                    retry_note=quality_retry_note(retry_error, item.tokenized),
                )
                attempts += 1

        target = build_target_from_translation(item.tokenized, translated_text)
        validate_target(item.source, target)
        return WorkResult(
            item=item,
            translation=translated_text,
            origin="openai",
            ok=True,
            api_seconds=time.perf_counter() - started,
            attempts=attempts,
        )
    except Exception as exc:  # noqa: BLE001
        return WorkResult(
            item=item,
            translation=None,
            origin="openai",
            ok=False,
            error=str(exc),
            api_seconds=time.perf_counter() - started,
            attempts=attempts,
        )


def apply_result(result: WorkResult, args: argparse.Namespace, cache_db: SQLiteCache) -> None:
    if not result.ok or result.translation is None:
        return
    target = build_target_from_translation(result.item.tokenized, result.translation)
    validate_target(result.item.source, target)
    target.set("state", "translated" if args.provider != "stub" and not args.dry_run else "needs-review")
    target.set(f"{{{CP_NS}}}translated-by", result.origin)
    replace_or_insert_target(result.item.unit, target)
    if result.origin == "openai":
        cache_db.put_ok(
            result.item.unit_id,
            result.item.source_hash,
            args.model,
            result.item.tokenized.text,
            result.translation,
        )


def translate_locale(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)

    glossary = load_glossary(args.glossary, args.phrases)
    cache_db = SQLiteCache(args.cache_db)
    fallback_dbs = [(path, SQLiteCache(path)) for path in args.fallback_cache_db if path.exists()]

    tree = ET.parse(args.input)
    items = collect_work_items(tree, args, glossary)
    translator = OpenAITranslator(args.model) if args.provider == "openai" else None

    processed = 0
    translated = 0
    cached = 0
    failed: list[dict[str, str]] = []
    api_seconds = 0.0
    pending_api: list[WorkItem] = []

    print(
        f"Starting parallel translation: input={args.input} provider={args.provider} "
        f"model={args.model} selected={len(items)} concurrency={args.concurrency}",
        flush=True,
    )

    for item in items:
        cached_translation = cache_db.get(item.unit_id, item.source_hash, args.model)
        origin = "cache"
        if cached_translation is None:
            for fallback_path, fallback_db in fallback_dbs:
                fallback_hash = source_hash(args.fallback_model, item.tokenized.text, glossary)
                cached_translation = fallback_db.get(item.unit_id, fallback_hash, args.fallback_model)
                if cached_translation is not None:
                    origin = f"fallback:{fallback_path.name}"
                    break

        if cached_translation is not None:
            apply_result(WorkResult(item, cached_translation, origin, True), args, cache_db)
            processed += 1
            translated += 1
            cached += 1
            if args.progress:
                print(f"[{processed}/{len(items)}] {item.unit_id} ok origin={origin} cache_hits={cached}", flush=True)
            continue

        fixed = fixed_phrase_translation(item.tokenized.text, glossary)
        if fixed is not None:
            apply_result(WorkResult(item, fixed, "fixed", True), args, cache_db)
            cache_db.put_ok(item.unit_id, item.source_hash, args.model, item.tokenized.text, fixed)
            processed += 1
            translated += 1
            if args.progress:
                print(f"[{processed}/{len(items)}] {item.unit_id} ok origin=fixed cache_hits={cached}", flush=True)
            continue

        if args.dry_run or args.provider == "stub":
            apply_result(WorkResult(item, item.tokenized.text, "dry-run", True), args, cache_db)
            processed += 1
            translated += 1
            if args.progress:
                print(f"[{processed}/{len(items)}] {item.unit_id} ok origin=dry-run cache_hits={cached}", flush=True)
            continue

        pending_api.append(item)

    if pending_api and translator is not None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            future_map = {
                executor.submit(translate_one, item, translator, glossary, args.model, args.retries): item
                for item in pending_api
            }
            for future in concurrent.futures.as_completed(future_map):
                result = future.result()
                processed += 1
                api_seconds += result.api_seconds
                if result.ok:
                    apply_result(result, args, cache_db)
                    translated += 1
                    if args.progress:
                        print(
                            f"[{processed}/{len(items)}] {result.item.unit_id} ok origin=openai "
                            f"translated={translated} cache_hits={cached} failed={len(failed)} "
                            f"api={format_seconds(result.api_seconds)} attempts={result.attempts}",
                            flush=True,
                        )
                else:
                    failed.append({"id": result.item.unit_id, "error": result.error})
                    cache_db.put_attempt(
                        result.item.unit_id,
                        result.item.source_hash,
                        args.model,
                        "failed",
                        result.error,
                        int(result.api_seconds * 1000),
                    )
                    if args.progress:
                        print(
                            f"[{processed}/{len(items)}] {result.item.unit_id} failed: {result.error}",
                            file=sys.stderr,
                            flush=True,
                        )

                if not args.dry_run and args.checkpoint_every and processed % args.checkpoint_every == 0:
                    partial_path = checkpoint_path(args.output)
                    checkpoint_started = time.perf_counter()
                    write_tree(partial_path, tree)
                    print(f"Checkpoint written: {partial_path} in {format_seconds(time.perf_counter() - checkpoint_started)}", flush=True)

    if not args.dry_run:
        write_tree(args.output, tree)

    report = {
        "input": str(args.input),
        "output": str(args.output),
        "provider": args.provider,
        "model": args.model,
        "cache_db": str(args.cache_db),
        "concurrency": args.concurrency,
        "processed": processed,
        "translated": translated,
        "cached": cached,
        "failed": failed,
        "timings_seconds": {
            "api_sum": api_seconds,
            "wall_total": time.perf_counter() - started,
        },
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Processed: {processed}")
    print(f"Translated/prepared: {translated}")
    print(f"Cache hits: {cached}")
    print(f"Failed: {len(failed)}")
    print(f"API time sum: {format_seconds(api_seconds)}")
    print(f"Wall time: {format_seconds(time.perf_counter() - started)}")
    if not args.dry_run:
        print(f"Output: {args.output}")
    if args.report:
        print(f"Report: {args.report}")
    return 0 if not failed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate XLF units using SQLite cache and parallel workers.")
    parser.add_argument("--input", type=Path, default=Path("output/pt_BR.xlf"))
    parser.add_argument("--output", type=Path, default=Path("output/pt_BR.ai.sqlite.xlf"))
    parser.add_argument("--glossary", type=Path, default=Path("glossary/pt_BR_terms.json"))
    parser.add_argument("--phrases", type=Path, default=Path("glossary/pt_BR_phrases.json"))
    parser.add_argument("--cache-db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--fallback-cache-db", type=Path, nargs="*", default=[])
    parser.add_argument("--fallback-model", default="gpt-5.4-mini")
    parser.add_argument("--report", type=Path, default=Path("cache/ai_translate_sqlite_report.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--provider", choices=("stub", "openai"), default="stub")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--mode", choices=("pending", "all"), default="pending")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--checkpoint-every", type=int)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--no-progress", dest="progress", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(progress=True)
    args = parser.parse_args()

    load_env_file(args.env_file)
    return translate_locale(args)


if __name__ == "__main__":
    raise SystemExit(main())
