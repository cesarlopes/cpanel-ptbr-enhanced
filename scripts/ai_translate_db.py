"""Translate pending locale units directly from SQLite.

This is the database-first translation workflow. XLF files remain the import
and export format, while day-to-day translation progress is stored in SQLite.
The script reads `locale_units`, writes API cache rows to `translations`, and
stores usable targets in `locale_targets` so the PHP UI and `build_locale.py
--from-db` can use the results immediately.
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

from refresh_locale_status import ensure_status_schema, refresh_status


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
CP_NS = "tag:cpanel.net,2012-01:translate"
TOKEN_RE = re.compile(r"__XLF_TAG_(\d+)__")
MOJIBAKE_MARKERS = ("Ãƒ", "Ã‚", "Ã¢â‚¬", "ï¿½")
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
    source_hash: str
    source_text: str
    source_xml: str
    tokenized: TokenizedSource
    cache_hash: str


@dataclass
class WorkResult:
    item: WorkItem
    translation: str | None
    target_xml: str | None
    target_text: str | None
    origin: str
    ok: bool
    error: str = ""
    api_seconds: float = 0.0
    attempts: int = 0


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.ensure_cache_tables()

    def ensure_cache_tables(self) -> None:
        with self._lock:
            self.conn.executescript(
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
                );

                CREATE TABLE IF NOT EXISTS translation_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            ensure_status_schema(self.conn)
            self.conn.commit()

    def ensure_locale_tables(self) -> None:
        missing = [
            name
            for name in ("locale_units", "locale_targets")
            if self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
            is None
        ]
        if missing:
            raise RuntimeError("missing locale DB tables: " + ", ".join(missing) + ". Run scripts/import_locale_to_db.py first.")

    def cache_get(self, unit_id: str, cache_hash: str, model: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT translation
                FROM translations
                WHERE id = ? AND source_hash = ? AND model = ? AND status = 'ok'
                """,
                (unit_id, cache_hash, model),
            ).fetchone()
        return row[0] if row else None

    def cache_put_ok(self, item: WorkItem, model: str, translation: str) -> None:
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
                (item.unit_id, item.cache_hash, model, item.tokenized.text, translation),
            )
            self.conn.commit()

    def put_attempt(self, item: WorkItem, model: str, status: str, error: str, duration_ms: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO translation_attempts (id, source_hash, model, status, error, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (item.unit_id, item.cache_hash, model, status, error, duration_ms),
            )
            self.conn.commit()

    def target_exists(self, item: WorkItem, mode: str) -> bool:
        if mode == "all":
            return False
        row = self.conn.execute(
            """
            SELECT 1
            FROM locale_targets
            WHERE unit_id = ?
              AND source_hash = ?
              AND origin IN ('manual', 'ai_cache', 'cpanel')
              AND quality_status IN ('valid', 'approved')
            LIMIT 1
            """,
            (item.unit_id, item.source_hash),
        ).fetchone()
        return row is not None

    def upsert_locale_target(
        self,
        item: WorkItem,
        *,
        target_text: str,
        target_xml: str,
        origin: str,
        provider: str,
        model: str,
        reviewed: bool = False,
    ) -> None:
        attrs = {
            "state": "translated",
            f"{{{CP_NS}}}translated-by": f"{provider}:{model}" if model else provider,
        }
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO locale_targets (
                    unit_id, source_hash, target, target_xml, target_attrs_json,
                    provider, model, origin, quality_status, is_reviewed, source_file
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, 'ai_translate_db')
                ON CONFLICT(unit_id, source_hash, origin, provider, model, source_file)
                DO UPDATE SET
                    target = excluded.target,
                    target_xml = excluded.target_xml,
                    target_attrs_json = excluded.target_attrs_json,
                    quality_status = 'valid',
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item.unit_id,
                    item.source_hash,
                    target_text,
                    target_xml,
                    json.dumps(attrs, ensure_ascii=False, sort_keys=True),
                    provider,
                    model,
                    origin,
                    1 if reviewed else 0,
                ),
            )
            refresh_status(self.conn, item.unit_id, ensure_schema=False)
            self.conn.commit()


class OpenAICompatibleTranslator:
    _local = threading.local()

    def __init__(self, model: str, provider: str) -> None:
        self.model = model
        self.provider = provider
        if provider == "xai":
            self.api_key = os.environ.get("XAI_API_KEY")
            self.base_url = "https://api.x.ai/v1"
            env_name = "XAI_API_KEY"
        else:
            self.api_key = os.environ.get("OPENAI_API_KEY")
            self.base_url = None
            env_name = "OPENAI_API_KEY"
        if not self.api_key:
            raise RuntimeError(f"{env_name} is not set. Add it to .env or export it in your shell.")

    def client(self) -> Any:
        if not hasattr(self._local, "client"):
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Install the OpenAI SDK first: pip install openai") from exc
            self._local.client = OpenAI(api_key=self.api_key, base_url=self.base_url) if self.base_url else OpenAI(api_key=self.api_key)
        return self._local.client

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

        response = self.client().responses.create(
            model=self.model,
            instructions=instructions,
            input=f"Unit ID: {unit_id}\nSource: {source}",
        )
        return response.output_text.strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def text_content(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def tag_signature(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    return [local_name(child.tag) for child in element.iter() if child is not element]


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


def cache_source_hash(model: str, source: str, glossary: dict[str, Any]) -> str:
    payload = json.dumps(
        {"model": model, "source": source, "glossary": glossary},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_glossary(terms_path: Path, phrases_path: Path) -> dict[str, Any]:
    glossary = load_json(terms_path)
    glossary["phrases"] = load_json(phrases_path)
    return glossary


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fixed_phrase_translation(source: str, glossary: dict[str, Any]) -> str | None:
    return glossary.get("phrases", {}).get(source)


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
            "Your previous answer contained mojibake or encoding artifacts such as Ãƒ, Ã‚, Ã¢â‚¬, or ï¿½. "
            "Return proper UTF-8 Brazilian Portuguese characters, for example: ação, usuário, domínio, não."
        )
    return "Your previous answer failed validation. Preserve all placeholders and inline tokens exactly."


def format_seconds(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.2f}s"


def collect_items(store: SQLiteStore, args: argparse.Namespace, glossary: dict[str, Any]) -> list[WorkItem]:
    scope_clause = "u.canonical = 1" if args.scope == "canonical" else "u.extended = 1"
    ready_expr = """
        EXISTS (
            SELECT 1
            FROM locale_targets bt
            WHERE bt.unit_id = u.unit_id
              AND bt.source_hash = u.source_hash
              AND bt.quality_status IN ('valid', 'approved')
              AND bt.origin IN ('manual', 'ai_cache', 'cpanel')
        )
    """
    best_origin_expr = """
        (
            SELECT t.origin
            FROM locale_targets t
            WHERE t.unit_id = u.unit_id
              AND t.source_hash = u.source_hash
              AND t.quality_status IN ('valid', 'approved')
            ORDER BY
              CASE
                WHEN t.origin = 'manual' AND t.is_reviewed = 1 THEN 1
                WHEN t.origin = 'ai_cache' AND t.quality_status = 'approved' THEN 2
                WHEN t.origin = 'ai_cache' THEN 3
                WHEN t.origin = 'cpanel' THEN 4
                ELSE 5
              END,
              t.updated_at DESC,
              t.target_id DESC
            LIMIT 1
        )
    """
    where_parts = ["rn = 1"]
    params: dict[str, object] = {}
    if args.mode == "pending":
        where_parts.append("ready = 0")
    elif args.mode == "review-origin":
        if not args.review_origin:
            raise ValueError("--review-origin is required when --mode review-origin is used")
        where_parts.append("best_origin = :review_origin")
        params["review_origin"] = args.review_origin
    pending_clause = "WHERE " + " AND ".join(where_parts)
    limit_clause = "LIMIT :limit" if args.limit is not None else ""
    if args.limit is not None:
        params["limit"] = args.limit
    sql = f"""
        WITH ranked AS (
            SELECT
                u.unit_id,
                u.source_hash,
                u.source,
                u.source_xml,
                {ready_expr} AS ready,
                {best_origin_expr} AS best_origin,
                ROW_NUMBER() OVER (
                    PARTITION BY u.unit_id
                    ORDER BY
                        u.canonical DESC,
                        CASE WHEN {ready_expr} THEN 0 ELSE 1 END,
                        u.updated_at DESC,
                        u.source_hash DESC
                ) AS rn
            FROM locale_units u
            WHERE {scope_clause}
        )
        SELECT unit_id, source_hash, source, source_xml
        FROM ranked
        {pending_clause}
        ORDER BY unit_id ASC
        {limit_clause}
    """
    cursor = store.conn.execute(sql, params)
    rows = cursor.fetchall()

    items: list[WorkItem] = []
    for unit_id, locale_hash, source_text, source_xml in rows:
        source = ET.fromstring(source_xml)
        tokenized = tokenized_source(source)
        item = WorkItem(
            index=len(items) + 1,
            unit_id=unit_id,
            source_hash=locale_hash,
            source_text=source_text,
            source_xml=source_xml,
            tokenized=tokenized,
            cache_hash=cache_source_hash(args.model, tokenized.text, glossary),
        )
        items.append(item)
    return items


def translate_one(
    item: WorkItem,
    translator: OpenAICompatibleTranslator,
    glossary: dict[str, Any],
    retries: int,
    provider: str,
) -> WorkResult:
    started = time.perf_counter()
    attempts = 0
    source = ET.fromstring(item.source_xml)
    try:
        translated_text = translator.translate(item.tokenized.text, unit_id=item.unit_id, glossary=glossary)
        attempts += 1
        for _attempt in range(retries):
            try:
                target = build_target_from_translation(item.tokenized, translated_text)
                validate_target(source, target)
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
        validate_target(source, target)
        target.set("state", "translated")
        target.set(f"{{{CP_NS}}}translated-by", provider)
        return WorkResult(
            item=item,
            translation=translated_text,
            target_xml=ET.tostring(target, encoding="unicode"),
            target_text=text_content(target),
            origin=provider,
            ok=True,
            api_seconds=time.perf_counter() - started,
            attempts=attempts,
        )
    except Exception as exc:  # noqa: BLE001
        return WorkResult(
            item=item,
            translation=None,
            target_xml=None,
            target_text=None,
            origin=provider,
            ok=False,
            error=str(exc),
            api_seconds=time.perf_counter() - started,
            attempts=attempts,
        )


def apply_cached_translation(store: SQLiteStore, item: WorkItem, translation: str, origin: str, args: argparse.Namespace) -> None:
    source = ET.fromstring(item.source_xml)
    target = build_target_from_translation(item.tokenized, translation)
    validate_target(source, target)
    target.set("state", "translated")
    target.set(f"{{{CP_NS}}}translated-by", origin)
    store.upsert_locale_target(
        item,
        target_text=text_content(target),
        target_xml=ET.tostring(target, encoding="unicode"),
        origin="ai_cache" if origin != "fixed" else "manual",
        provider=origin,
        model=args.model if origin != "fixed" else "",
    )


def translate_db(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    ET.register_namespace("", XLIFF_NS)
    ET.register_namespace("cp", CP_NS)

    load_env_file(args.env_file)
    glossary = load_glossary(args.glossary, args.phrases)
    store = SQLiteStore(args.db)
    store.ensure_locale_tables()
    translator = OpenAICompatibleTranslator(args.model, args.provider) if args.provider in ("openai", "xai") else None
    items = collect_items(store, args, glossary)

    processed = 0
    translated = 0
    cache_hits = 0
    failed: list[dict[str, str]] = []
    api_seconds = 0.0
    pending_api: list[WorkItem] = []

    print(
        f"Starting DB translation: db={args.db} scope={args.scope} provider={args.provider} "
        f"model={args.model} selected={len(items)} concurrency={args.concurrency}",
        flush=True,
    )

    for item in items:
        cached = store.cache_get(item.unit_id, item.cache_hash, args.model)
        origin = "cache"
        if cached is None and args.fallback_model and args.fallback_model != args.model:
            fallback_hash = cache_source_hash(args.fallback_model, item.tokenized.text, glossary)
            cached = store.cache_get(item.unit_id, fallback_hash, args.fallback_model)
            origin = f"fallback:{args.fallback_model}" if cached is not None else origin

        if cached is not None:
            try:
                if not args.dry_run:
                    apply_cached_translation(store, item, cached, origin, args)
                processed += 1
                translated += 1
                cache_hits += 1
                if args.progress:
                    print(f"[{processed}/{len(items)}] {item.unit_id} ok origin={origin} cache_hits={cache_hits}", flush=True)
                continue
            except Exception as exc:  # noqa: BLE001
                failed.append({"id": item.unit_id, "error": str(exc)})
                processed += 1
                print(f"[{processed}/{len(items)}] {item.unit_id} failed cache validation: {exc}", file=sys.stderr, flush=True)
                continue

        fixed = fixed_phrase_translation(item.tokenized.text, glossary)
        if fixed is not None:
            try:
                if not args.dry_run:
                    apply_cached_translation(store, item, fixed, "fixed", args)
                    store.cache_put_ok(item, args.model, fixed)
                processed += 1
                translated += 1
                if args.progress:
                    print(f"[{processed}/{len(items)}] {item.unit_id} ok origin=fixed cache_hits={cache_hits}", flush=True)
                continue
            except Exception as exc:  # noqa: BLE001
                failed.append({"id": item.unit_id, "error": str(exc)})
                processed += 1
                print(f"[{processed}/{len(items)}] {item.unit_id} failed fixed validation: {exc}", file=sys.stderr, flush=True)
                continue

        if args.dry_run or args.provider == "stub":
            processed += 1
            translated += 1
            if args.progress:
                print(f"[{processed}/{len(items)}] {item.unit_id} prepared origin=dry-run cache_hits={cache_hits}", flush=True)
            continue

        pending_api.append(item)

    if pending_api and translator is not None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {
                executor.submit(translate_one, item, translator, glossary, args.retries, args.provider): item
                for item in pending_api
            }
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                processed += 1
                api_seconds += result.api_seconds
                if result.ok and result.translation and result.target_xml and result.target_text is not None:
                    store.cache_put_ok(result.item, args.model, result.translation)
                    store.upsert_locale_target(
                        result.item,
                        target_text=result.target_text,
                        target_xml=result.target_xml,
                        origin="ai_cache",
                        provider=args.provider,
                        model=args.model,
                    )
                    translated += 1
                    if args.progress:
                        print(
                            f"[{processed}/{len(items)}] {result.item.unit_id} ok origin={args.provider} "
                            f"translated={translated} cache_hits={cache_hits} failed={len(failed)} "
                            f"api={format_seconds(result.api_seconds)} attempts={result.attempts}",
                            flush=True,
                        )
                else:
                    failed.append({"id": result.item.unit_id, "error": result.error})
                    store.put_attempt(result.item, args.model, "failed", result.error, int(result.api_seconds * 1000))
                    print(f"[{processed}/{len(items)}] {result.item.unit_id} failed: {result.error}", file=sys.stderr, flush=True)

                if args.checkpoint_every and processed % args.checkpoint_every == 0:
                    print(f"Checkpoint: processed={processed} translated={translated} failed={len(failed)}", flush=True)

    report = {
        "db": str(args.db),
        "scope": args.scope,
        "provider": args.provider,
        "model": args.model,
        "fallback_model": args.fallback_model,
        "mode": args.mode,
        "review_origin": args.review_origin,
        "processed": processed,
        "translated": translated,
        "cache_hits": cache_hits,
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
    print(f"Cache hits: {cache_hits}")
    print(f"Failed: {len(failed)}")
    print(f"API time sum: {format_seconds(api_seconds)}")
    print(f"Wall time: {format_seconds(time.perf_counter() - started)}")
    if args.report:
        print(f"Report: {args.report}")
    return 0 if not failed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate pending locale units directly from SQLite.")
    parser.add_argument("--db", type=Path, default=Path("cache/translations.sqlite"))
    parser.add_argument("--glossary", type=Path, default=Path("glossary/pt_BR_terms.json"))
    parser.add_argument("--phrases", type=Path, default=Path("glossary/pt_BR_phrases.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--report", type=Path, default=Path("cache/ai_translate_db_report.json"))
    parser.add_argument("--provider", choices=("stub", "openai", "xai"), default="stub")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--fallback-model")
    parser.add_argument("--scope", choices=("canonical", "extended"), default="canonical")
    parser.add_argument("--mode", choices=("pending", "all", "review-origin"), default="pending")
    parser.add_argument(
        "--review-origin",
        choices=("manual", "ai_cache", "cpanel"),
        help="When --mode review-origin is used, select units whose current best target has this origin.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--checkpoint-every", type=int)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-progress", dest="progress", action="store_false")
    parser.set_defaults(progress=True)
    args = parser.parse_args()
    if args.mode == "review-origin" and not args.review_origin:
        parser.error("--review-origin is required when --mode review-origin is used")

    return translate_db(args)


if __name__ == "__main__":
    raise SystemExit(main())
