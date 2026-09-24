"""Deterministic Human review / Sources footer from reconciled facts."""

from __future__ import annotations

import re

from agent_pipeline.schema import Account, ReconciledFacts, SourcedValue

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


def render_human_review(facts: ReconciledFacts) -> str:
    lines = ["## Human review", "", "### Conflicts"]
    if facts.conflicts:
        for conflict in facts.conflicts:
            lines.append(f"- **{conflict.field}**: {conflict.details}")
    else:
        lines.append("No open conflicts.")

    lines.extend(["", "### Review items"])
    if facts.review_items:
        for item in facts.review_items:
            lines.append(f"- {item}")
    else:
        lines.append("No pending review items.")
    return "\n".join(lines)


def _source_label(source_file: str | None, role: str | None, as_of: str | None) -> str:
    if source_file:
        label = source_file
    elif role:
        label = role
    else:
        label = "unknown"
    if as_of:
        label = f"{label} @ {as_of}"
    return label


def _fact_label(entry: SourcedValue) -> str:
    return _source_label(entry.source_file, entry.source, entry.as_of)


def _account_label(account: Account) -> str:
    return _source_label(account.value_source_file, account.value_source, account.as_of)


def render_sources(facts: ReconciledFacts, retrieve_roles: dict[str, str] | None = None) -> str:
    """Build Sources table. Prefer source_file over classifier role."""
    _ = retrieve_roles
    lines = [
        "## Sources",
        "",
        "| Fact area | Source |",
        "|-----------|--------|",
    ]
    for name, entry in sorted(facts.facts.items()):
        conflict = " (conflict)" if entry.conflict else ""
        lines.append(f"| {name.replace('_', ' ')} | {_fact_label(entry)}{conflict} |")

    for account in facts.accounts:
        conflict = " (conflict)" if account.value_conflict else ""
        lines.append(
            f"| account {account.account_id} value | {_account_label(account)}{conflict} |"
        )

    if len(lines) == 4:
        lines.append("| (none) | (none) |")
    return "\n".join(lines)


def append_hitl_footer(
    report: str,
    facts: ReconciledFacts,
    retrieve_roles: dict[str, str] | None = None,
) -> str:
    merged = facts.add_review_tags(collect_review_tags(report))
    body = report.rstrip() + "\n\n"
    body += render_human_review(merged) + "\n\n"
    body += render_sources(merged, retrieve_roles) + "\n"
    return body
