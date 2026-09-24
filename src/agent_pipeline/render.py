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


def _type_tokens(text: str) -> set[str]:
    """Generic account-type tokens from free text (no platform name allowlists)."""
    lower = text.lower()
    tokens: set[str] = set()
    for word in (
        "isa",
        "gia",
        "sipp",
        "pension",
        "bond",
        "cash",
        "offshore",
        "joint",
    ):
        if word in lower:
            tokens.add(word)
    # Stocks & Shares ISA etc.
    if "stocks" in lower and "shares" in lower:
        tokens.add("isa")
    if "general investment" in lower:
        tokens.add("gia")
    return tokens


def account_in_scope(account: dict[str, Any], covered: str) -> bool:
    """Deterministic match of a db account against request accounts_covered text."""
    if not covered or not str(covered).strip():
        return True
    covered_tokens = _type_tokens(str(covered))
    if not covered_tokens:
        return True

    typ = str(account.get("type") or "")
    aid = str(account.get("account_id") or "")
    account_tokens = _type_tokens(f"{typ} {aid}")
    if not account_tokens:
        # Unknown type — include when coverage is non-empty so we do not hide
        # accounts the matcher cannot classify; REVIEW is for humans.
        return True
    return bool(account_tokens.intersection(covered_tokens))


def resolve_scoped_accounts(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter accounts: skip closed; keep in-scope; optional new-account row."""
    covered = str(_fact_value(facts, "accounts_covered") or "")
    accounts = facts.get("accounts") or []
    scoped: list[dict[str, Any]] = []
    for acc in accounts:
        status = str(acc.get("status") or "").lower()
        if status == "closed":
            continue
        if acc.get("synthetic"):
            scoped.append(acc)
            continue
        if account_in_scope(acc, covered):
            scoped.append(acc)

    covered_l = covered.lower()
    wants_new = "new" in covered_l and (
        "account" in covered_l or "joint" in covered_l
    )
    if wants_new and not any(a.get("synthetic") for a in scoped):
        scoped.append(
            {
                "account_id": "New joint account",
                "owner": "Joint",
                "type": "To be opened",
                "value": "n/a",
                "synthetic": True,
            }
        )
    return scoped


def _format_conflict_value(acc: dict[str, Any]) -> str:
    chosen = format_money(acc.get("value"))
    as_of = acc.get("as_of")
    src = acc.get("value_source") or "draft"
    approx = "approx., " if acc.get("approximate") else ""
    date_bit = f" @ {as_of}" if as_of else ""
    primary = f"{chosen} ({approx}{src}{date_bit})".replace("  ", " ")

    alts = acc.get("value_alternates") or []
    other = next(
        (a for a in alts if a.get("source_role") != acc.get("value_source")),
        alts[0] if alts else None,
    )
    if other:
        other_val = format_money(other.get("value"))
        other_as = other.get("as_of")
        other_src = other.get("source_role") or "other"
        other_date = f" @ {other_as}" if other_as else ""
        primary = f"{primary} [REVIEW: {other_src} {other_val}{other_date}]"
    elif acc.get("value_conflict"):
        primary = f"{primary} [REVIEW: value conflict]"
    elif acc.get("approximate"):
        primary = f"{primary} [REVIEW: approximate figure]"
    return primary


def render_holdings_table(facts: dict[str, Any]) -> str:
    accounts = resolve_scoped_accounts(facts)
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
        if acc.get("synthetic"):
            value = str(acc.get("value") or "n/a")
        elif acc.get("value") is None:
            value = f"[REVIEW: {aid} value]"
        elif acc.get("value_conflict") or acc.get("approximate") or acc.get(
            "value_alternates"
        ):
            value = _format_conflict_value(acc)
        else:
            value = format_money(acc.get("value"))
        lines.append(f"| {aid} | {owner} | {typ} | {value} |")
    return "\n".join(lines)


def render_fees(facts: dict[str, Any]) -> str:
    lines = [
        "| Charge | Rate |",
        "|--------|------|",
    ]
    initial = _fact_value(facts, "initial_charge")
    if initial not in (None, ""):
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
        lines.append(
            f"- {field}: {entry.get('value')!r} (source={entry.get('source')}){flag}"
        )
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
