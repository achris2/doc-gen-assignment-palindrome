"""Deterministic Human review / Sources footer from reconciled facts."""

from typing import Any


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


def render_sources(facts: dict[str, Any], retrieve_roles: dict[str, str] | None = None) -> str:
    lines = [
        "## Sources",
        "",
        "| Fact area | Source |",
        "|-----------|--------|",
    ]

    for field, entry in sorted((facts.get("facts") or {}).items()):
        if not isinstance(entry, dict):
            continue
        source = entry.get("source") or "unknown"
        as_of = entry.get("as_of")
        label = f"{source}" + (f" @ {as_of}" if as_of else "")
        conflict = " (conflict)" if entry.get("conflict") else ""
        lines.append(f"| {field.replace('_', ' ')} | {label}{conflict} |")

    for acc in facts.get("accounts") or []:
        aid = acc.get("account_id", "?")
        src = acc.get("value_source") or "unknown"
        as_of = acc.get("as_of")
        label = f"{src}" + (f" @ {as_of}" if as_of else "")
        conflict = " (conflict)" if acc.get("value_conflict") else ""
        lines.append(f"| account {aid} value | {label}{conflict} |")

    if retrieve_roles:
        for name, role in sorted(retrieve_roles.items()):
            if role in {"request", "meeting", "db"}:
                lines.append(f"| retrieve:{role} | {name} |")

    if len(lines) == 4:
        lines.append("| (none) | (none) |")

    return "\n".join(lines)


def append_hitl_footer(
    report: str,
    facts: dict[str, Any],
    retrieve_roles: dict[str, str] | None = None,
) -> str:
    body = report.rstrip() + "\n\n"
    body += render_human_review(facts) + "\n\n"
    body += render_sources(facts, retrieve_roles) + "\n"
    return body
