"""Deterministic Human review / Sources footer from reconciled facts."""

from __future__ import annotations

import re
from typing import Any

_REVIEW_TAG = re.compile(r"\[REVIEW:[^\]]+\]")


def collect_review_tags(text: str) -> list[str]:
    """Return unique [REVIEW:…] tags in order of first appearance."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in _REVIEW_TAG.findall(text):
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def merge_body_reviews_into_facts(report_body: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Copy facts with body [REVIEW] tags merged into review_items."""
    merged = dict(facts)
    items = list(facts.get("review_items") or [])
    seen = set(items)
    for tag in collect_review_tags(report_body):
        if tag not in seen:
            items.append(tag)
            seen.add(tag)
    merged["review_items"] = items
    return merged


def render_human_review(facts: dict[str, Any]) -> str:
    conflicts = facts.get("conflicts") or []
    review_items = facts.get("review_items") or []

    lines = ["## Human review", "", "### Conflicts"]
    if conflicts:
        for c in conflicts:
            field = c.get("field", "unknown")
            details = c.get("details", "")
            lines.append(f"- **{field}**: {details}")
    else:
        lines.append("No open conflicts.")

    lines.extend(["", "### Review items"])
    if review_items:
        for item in review_items:
            lines.append(f"- {item}")
    else:
        lines.append("No pending review items.")

    return "\n".join(lines)


def _source_label(
    entry: dict[str, Any], *, role_key: str = "source", file_key: str = "source_file"
) -> str:
    source_file = entry.get(file_key)
    role = entry.get(role_key)
    as_of = entry.get("as_of")
    if source_file:
        label = str(source_file)
    elif role:
        label = str(role)
    else:
        label = "unknown"
    if as_of:
        label = f"{label} @ {as_of}"
    return label


def render_sources(
    facts: dict[str, Any], retrieve_roles: dict[str, str] | None = None
) -> str:
    """Build Sources table. Prefer source_file over classifier role."""
    _ = retrieve_roles
    lines = [
        "## Sources",
        "",
        "| Fact area | Source |",
        "|-----------|--------|",
    ]

    for field, entry in sorted((facts.get("facts") or {}).items()):
        if not isinstance(entry, dict):
            continue
        label = _source_label(entry)
        conflict = " (conflict)" if entry.get("conflict") else ""
        lines.append(f"| {field.replace('_', ' ')} | {label}{conflict} |")

    for acc in facts.get("accounts") or []:
        aid = acc.get("account_id", "?")
        label = _source_label(
            acc, role_key="value_source", file_key="value_source_file"
        )
        conflict = " (conflict)" if acc.get("value_conflict") else ""
        lines.append(f"| account {aid} value | {label}{conflict} |")

    if len(lines) == 4:
        lines.append("| (none) | (none) |")

    return "\n".join(lines)


def append_hitl_footer(
    report: str,
    facts: dict[str, Any],
    retrieve_roles: dict[str, str] | None = None,
) -> str:
    merged = merge_body_reviews_into_facts(report, facts)
    body = report.rstrip() + "\n\n"
    body += render_human_review(merged) + "\n\n"
    body += render_sources(merged, retrieve_roles) + "\n"
    return body
