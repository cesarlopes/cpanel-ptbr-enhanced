"""Run offline QA checks against a translated XLF/XLIFF file.

The script does not modify translations. It reports suspicious targets that
deserve manual review after AI translation.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from xml.etree import ElementTree as ET


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
MOJIBAKE_MARKERS = ("Ãƒ", "Ã‚", "Ã¢â‚¬", "Ã¢â‚¬Â¦", "ï¿½", "�")
LEAKED_TOKEN_RE = re.compile(r"__XLF_TAG_\d+__")
COMMON_ENGLISH_WORDS = {
    "account",
    "accounts",
    "backup",
    "change",
    "click",
    "create",
    "delete",
    "disabled",
    "domain",
    "domains",
    "enabled",
    "error",
    "file",
    "folder",
    "password",
    "restore",
    "settings",
    "successfully",
    "user",
    "users",
}
INVARIANT_TARGETS = {
    "%s byte",
    "%s bytes",
    "%s inode",
    "%s inodes",
    "API",
    "DNS",
    "FTP",
    "HTML",
    "IMAP",
    "MySQL",
    "PHP",
    "SSH",
    "SSL",
    "TLS",
    "WHM",
    "cPanel",
}
UNACCENTED_TERMS = {
    "acao": "ação",
    "acoes": "ações",
    "autenticacao": "autenticação",
    "configuracao": "configuração",
    "configuracoes": "configurações",
    "dominio": "domínio",
    "dominios": "domínios",
    "informacao": "informação",
    "informacoes": "informações",
    "nao": "não",
    "opcao": "opção",
    "opcoes": "opções",
    "permissao": "permissão",
    "permissoes": "permissões",
    "usuario": "usuário",
    "usuarios": "usuários",
}
SUSPICIOUS_PHRASES = {
    "quinze e quinze": "Use 'Aos 15 minutos de cada hora' for cron schedule context.",
    "mantê-los abaixo": "Usually better as 'escolha abaixo não mantê-los' or 'desative abaixo a retenção'.",
    "mantê-lo abaixo": "Usually better as 'escolha abaixo não mantê-lo' or 'desative abaixo a retenção'.",
    "apenas de email": "Review wording; 'somente de email' may be clearer depending on context.",
}


@dataclass
class Issue:
    id: str
    severity: str
    check: str
    message: str
    source: str
    target: str
    suggestion: str = ""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_child(unit: ET.Element, child_name: str) -> ET.Element | None:
    for child in unit:
        if local_name(child.tag) == child_name:
            return child
    return None


def text_content(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def tag_signature(element: ET.Element | None) -> list[str]:
    if element is None:
        return []
    return [local_name(child.tag) for child in element.iter() if child is not element]


def add_issue(
    issues: list[Issue],
    unit_id: str,
    severity: str,
    check: str,
    message: str,
    source: str,
    target: str,
    suggestion: str = "",
) -> None:
    issues.append(Issue(unit_id, severity, check, message, source, target, suggestion))


def check_unit(unit: ET.Element) -> list[Issue]:
    unit_id = unit.attrib.get("id", "<missing-id>")
    source_el = find_child(unit, "source")
    target_el = find_child(unit, "target")
    source = text_content(source_el)
    target = text_content(target_el)
    issues: list[Issue] = []

    if target_el is None:
        add_issue(issues, unit_id, "P1", "missing-target", "Missing target element.", source, target)
        return issues

    if any(marker in target for marker in MOJIBAKE_MARKERS):
        add_issue(issues, unit_id, "P1", "mojibake", "Target appears to contain encoding artifacts.", source, target)

    missing_placeholders = placeholders(source) - placeholders(target)
    if missing_placeholders:
        add_issue(
            issues,
            unit_id,
            "P1",
            "missing-placeholder",
            "Target is missing placeholders: " + ", ".join(sorted(missing_placeholders)),
            source,
            target,
        )

    if tag_signature(source_el) != tag_signature(target_el):
        add_issue(
            issues,
            unit_id,
            "P1",
            "tag-mismatch",
            f"Inline tag mismatch: source={tag_signature(source_el)} target={tag_signature(target_el)}",
            source,
            target,
        )

    if LEAKED_TOKEN_RE.search(target):
        add_issue(issues, unit_id, "P1", "leaked-token", "Internal __XLF_TAG_N__ token leaked into target.", source, target)

    if source and target and source == target and source not in INVARIANT_TARGETS:
        severity = "P3" if len(source) <= 20 else "P2"
        add_issue(issues, unit_id, severity, "target-equals-source", "Target is identical to source.", source, target)

    target_words = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", target)}
    remaining_english = sorted(target_words & COMMON_ENGLISH_WORDS)
    if remaining_english and source != target:
        add_issue(
            issues,
            unit_id,
            "P2",
            "english-remains",
            "Possible untranslated English words: " + ", ".join(remaining_english),
            source,
            target,
        )

    lowered_target = target.lower()
    for bad, good in UNACCENTED_TERMS.items():
        if re.search(rf"\b{re.escape(bad)}\b", lowered_target):
            add_issue(
                issues,
                unit_id,
                "P3",
                "missing-accent",
                f"Possible missing accent: '{bad}' -> '{good}'.",
                source,
                target,
                suggestion=good,
            )

    for phrase, suggestion in SUSPICIOUS_PHRASES.items():
        if phrase in lowered_target:
            add_issue(
                issues,
                unit_id,
                "P3",
                "suspicious-phrase",
                f"Suspicious phrase: '{phrase}'.",
                source,
                target,
                suggestion=suggestion,
            )

    return issues


def run_qa(path: Path) -> list[Issue]:
    tree = ET.parse(path)
    issues: list[Issue] = []
    for unit in tree.getroot().iter():
        if local_name(unit.tag) == "trans-unit":
            issues.extend(check_unit(unit))
    return issues


def write_json(path: Path, issues: list[Issue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(issue) for issue in issues], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_markdown(path: Path, issues: list[Issue], max_items: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1

    lines = [
        "# QA Report",
        "",
        "## Summary",
        "",
        f"- Total issues: {len(issues)}",
        f"- P1: {counts.get('P1', 0)}",
        f"- P2: {counts.get('P2', 0)}",
        f"- P3: {counts.get('P3', 0)}",
        "",
        "## Issues",
        "",
    ]

    severity_order = {"P1": 0, "P2": 1, "P3": 2}
    sorted_issues = sorted(issues, key=lambda item: (severity_order.get(item.severity, 9), item.id, item.check))
    for issue in sorted_issues[:max_items]:
        lines.extend(
            [
                f"### {issue.severity} {issue.id} - {issue.check}",
                "",
                issue.message,
                "",
                "Source:",
                "",
                "```text",
                issue.source,
                "```",
                "",
                "Target:",
                "",
                "```text",
                issue.target,
                "```",
                "",
            ]
        )
        if issue.suggestion:
            lines.extend(["Suggestion:", "", f"```text\n{issue.suggestion}\n```", ""])

    if len(issues) > max_items:
        lines.append(f"_Only first {max_items} issues are shown. See JSON report for all issues._")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline QA checks on translated XLF.")
    parser.add_argument("xlf", type=Path)
    parser.add_argument("--json", type=Path, default=Path("cache/qa_report.json"))
    parser.add_argument("--markdown", type=Path, default=Path("cache/qa_report.md"))
    parser.add_argument("--max-markdown-items", type=int, default=200)
    args = parser.parse_args()

    issues = run_qa(args.xlf)
    write_json(args.json, issues)
    write_markdown(args.markdown, issues, args.max_markdown_items)

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1

    print(f"QA complete: {args.xlf}")
    print(f"Total issues: {len(issues)}")
    print(f"P1: {counts.get('P1', 0)}")
    print(f"P2: {counts.get('P2', 0)}")
    print(f"P3: {counts.get('P3', 0)}")
    print(f"JSON report: {args.json}")
    print(f"Markdown report: {args.markdown}")
    return 0 if counts.get("P1", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
