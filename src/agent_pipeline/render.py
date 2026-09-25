"""Deterministic renderers for fact-bound report slots."""

from typing import Any

from agent_pipeline.schema import Account, ReconciledFacts


def format_money(value: Any) -> str:
    if value is None:
        return "[REVIEW: missing value]"
    if isinstance(value, (int, float)):
        return f"£{value:,.0f}" if float(value).is_integer() else f"£{value:,.2f}"
    text = str(value).strip()
    return text if text else "[REVIEW: missing value]"


def render_scope(facts: ReconciledFacts) -> str:
    covered = facts.fact_value("accounts_covered")
    if covered:
        return str(covered)
    return "[REVIEW: accounts covered]"


def _type_tokens(text: str) -> set[str]:
    """Generic account-type tokens from free text (no platform name allowlists)."""
    lower = text.lower()
    tokens: set[str] = set()
    for word in ("isa", "gia", "sipp", "pension", "bond", "cash", "offshore", "joint"):
        if word in lower:
            tokens.add(word)
    if "stocks" in lower and "shares" in lower:
        tokens.add("isa")
    if "general investment" in lower:
        tokens.add("gia")
    return tokens


def account_in_scope(account: Account, covered: str) -> bool:
    """Deterministic match of a db account against request accounts_covered text."""
    if not covered or not covered.strip():
        return True
    covered_tokens = _type_tokens(covered)
    if not covered_tokens:
        return True
    account_tokens = _type_tokens(f"{account.type or ''} {account.account_id}")
    if not account_tokens:
        return True
    return bool(account_tokens.intersection(covered_tokens))


def resolve_scoped_accounts(facts: ReconciledFacts) -> list[Account]:
    """Filter accounts: skip closed; keep in-scope; optional new-account row."""
    covered = str(facts.fact_value("accounts_covered") or "")
    scoped: list[Account] = []
    for account in facts.accounts:
        if str(account.status or "").lower() == "closed":
            continue
        if account.synthetic or account_in_scope(account, covered):
            scoped.append(account)

    covered_l = covered.lower()
    wants_new = "new" in covered_l and ("account" in covered_l or "joint" in covered_l)
    if wants_new and not any(account.synthetic for account in scoped):
        synthetic = Account(
            account_id="New joint account",
            owner="Joint",
            type="To be opened",
            value="n/a",
        )
        synthetic.mark_synthetic()
        synthetic._assigned.update({"owner", "type", "value"})
        scoped.append(synthetic)
    return scoped


def _format_conflict_value(account: Account) -> str:
    chosen = format_money(account.value)
    date_bit = f" @ {account.as_of}" if account.as_of else ""
    approx = "approx., " if account.approximate else ""
    src = account.value_source or "draft"
    primary = f"{chosen} ({approx}{src}{date_bit})".replace("  ", " ")

    other = next(
        (alt for alt in account.value_alternates if alt.source_role != account.value_source),
        account.value_alternates[0] if account.value_alternates else None,
    )
    if other:
        other_date = f" @ {other.as_of}" if other.as_of else ""
        other_src = other.source_role or "other"
        primary = f"{primary} [REVIEW: {other_src} {format_money(other.value)}{other_date}]"
    elif account.value_conflict:
        primary = f"{primary} [REVIEW: value conflict]"
    elif account.approximate:
        primary = f"{primary} [REVIEW: approximate figure]"
    return primary


def render_holdings_table(facts: ReconciledFacts) -> str:
    accounts = resolve_scoped_accounts(facts)
    lines = [
        "| Account | Owner | Type | Value |",
        "|---------|-------|------|-------|",
    ]
    if not accounts:
        lines.append("| [REVIEW: accounts] | — | — | — |")
        return "\n".join(lines)

    for account in accounts:
        aid = account.account_id or "—"
        owner = account.owner or "—"
        typ = account.type or "—"
        if account.synthetic:
            value = str(account.value or "n/a")
        elif account.value is None:
            value = f"[REVIEW: {aid} value]"
        elif account.value_conflict or account.approximate or account.value_alternates:
            value = _format_conflict_value(account)
        else:
            value = format_money(account.value)
        lines.append(f"| {aid} | {owner} | {typ} | {value} |")
    return "\n".join(lines)


def render_fees(facts: ReconciledFacts) -> str:
    lines = [
        "| Charge | Rate |",
        "|--------|------|",
    ]
    initial = facts.fact_value("initial_charge")
    if initial not in (None, ""):
        text = str(initial).strip()
        if text and text not in {"0", "0%", "0.0", "0.0%"}:
            lines.append(f"| Initial charge | {text} |")
    lines.append("| Platform fee | [REVIEW: platform fee] |")
    lines.append("| Ongoing advice charge | [REVIEW: advice fee] |")
    return "\n".join(lines)


_FUNDING_LABELS = {
    "received_proceeds": "Received",
    "loan_repayment": "Committed repayment",
    "contingent_proceeds": "Contingent, not available",
}


def render_funding(facts: ReconciledFacts) -> str:
    """Money that is not a custody balance and not an investment action."""
    lines = []
    seen: set[tuple[str, Any]] = set()
    for item in facts.recorded:
        label = _FUNDING_LABELS.get(item.kind or "")
        if label is None:
            continue
        value = item.draft.value
        key = (item.kind or "", value)
        if key in seen:
            continue
        seen.add(key)
        quote = (item.draft.quote or "").strip()
        line = f"- {label}: {format_money(value)}"
        if quote:
            line += f". {quote}"
        lines.append(line)
    return "\n".join(lines)


def render_cgt_statement(_facts: ReconciledFacts) -> str:
    return (
        "The disposal may create a capital gains tax liability, which will be "
        "assessed against the annual exempt amount. [REVIEW: CGT figure]"
    )


def facts_context_block(facts: ReconciledFacts) -> str:
    """Compact CASE FACTS block for narrative LLM prompts."""
    lines = ["CASE FACTS (authoritative draft — do not invent beyond this):"]
    lines.append(f"- selling: {facts.selling}")
    for name, entry in sorted(facts.facts.items()):
        flag = " [CONFLICT]" if entry.conflict else ""
        lines.append(f"- {name}: {entry.value!r} (source={entry.source}){flag}")
    for account in facts.accounts:
        flag = " [CONFLICT]" if account.value_conflict else ""
        lines.append(
            f"- account {account.account_id}: type={account.type!r} "
            f"owner={account.owner!r} value={account.value!r} "
            f"as_of={account.as_of!r}{flag}"
        )
    if facts.conflicts:
        lines.append("Conflicts:")
        for conflict in facts.conflicts:
            lines.append(f"- {conflict.field}: {conflict.details}")
    return "\n".join(lines)
