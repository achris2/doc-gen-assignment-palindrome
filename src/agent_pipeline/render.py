"""Deterministic renderers for fact-bound report slots."""

from typing import Any


def _fact_value(facts: dict[str, Any], field: str, default: str = "") -> Any:
    entry = (facts.get("facts") or {}).get(field)
    if isinstance(entry, dict):
        return entry.get("value", default)
    return default


def format_money(value: Any) -> str:
    if value is None:
        return "[REVIEW: missing value]"
    if isinstance(value, (int, float)):
        return f"£{value:,.0f}" if float(value).is_integer() else f"£{value:,.2f}"
    text = str(value).strip()
    return text if text else "[REVIEW: missing value]"


def render_scope(facts: dict[str, Any]) -> str:
    covered = _fact_value(facts, "accounts_covered")
    if covered:
        return str(covered)
    return "[REVIEW: accounts covered]"


def render_holdings_table(facts: dict[str, Any]) -> str:
    accounts = facts.get("accounts") or []
    lines = [
        "| Account | Owner | Type | Value |",
        "|---------|-------|------|-------|",
    ]
    if not accounts:
        lines.append("| [REVIEW: accounts] | — | — | — |")
        return "\n".join(lines)

    for acc in accounts:
        aid = acc.get("account_id") or "—"
        owner = acc.get("owner") or "—"
        typ = acc.get("type") or "—"
        if acc.get("value") is None or acc.get("needs_review") and acc.get("value") is None:
            value = f"[REVIEW: {aid} value]"
        else:
            value = format_money(acc.get("value"))
            if acc.get("value_conflict"):
                value = f"{value} [REVIEW: value conflict]"
        lines.append(f"| {aid} | {owner} | {typ} | {value} |")
    return "\n".join(lines)


def render_fees(facts: dict[str, Any]) -> str:
    lines = [
        "| Charge | Rate |",
        "|--------|------|",
    ]
    initial = _fact_value(facts, "initial_charge")
    if initial not in (None, ""):
        # Only show non-zero / explicit initial charges as their own row
        text = str(initial).strip()
        if text and text not in {"0", "0%", "0.0", "0.0%"}:
            lines.append(f"| Initial charge | {text} |")
    lines.append("| Platform fee | [REVIEW: platform fee] |")
    lines.append("| Ongoing advice charge | [REVIEW: advice fee] |")
    return "\n".join(lines)


def render_cgt_statement(_facts: dict[str, Any]) -> str:
    return (
        "The disposal may create a capital gains tax liability, which will be "
        "assessed against the annual exempt amount. [REVIEW: CGT figure]"
    )


def facts_context_block(facts: dict[str, Any]) -> str:
    """Compact CASE FACTS block for narrative LLM prompts."""
    lines = ["CASE FACTS (authoritative draft — do not invent beyond this):"]
    lines.append(f"- selling: {facts.get('selling')}")
    for field, entry in sorted((facts.get("facts") or {}).items()):
        if not isinstance(entry, dict):
            continue
        flag = " [CONFLICT]" if entry.get("conflict") else ""
        lines.append(f"- {field}: {entry.get('value')!r} (source={entry.get('source')}){flag}")
    for acc in facts.get("accounts") or []:
        flag = " [CONFLICT]" if acc.get("value_conflict") else ""
        lines.append(
            f"- account {acc.get('account_id')}: type={acc.get('type')!r} "
            f"owner={acc.get('owner')!r} value={acc.get('value')!r} "
            f"as_of={acc.get('as_of')!r}{flag}"
        )
    if facts.get("conflicts"):
        lines.append("Conflicts:")
        for c in facts["conflicts"]:
            lines.append(f"- {c.get('field')}: {c.get('details')}")
    return "\n".join(lines)
