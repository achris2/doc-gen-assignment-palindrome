"""Deterministic renderers for fact-bound report slots."""

import re
from typing import Any

from agent_pipeline.schema import Account, CaseDocument, MoneyAvailability, ReconciledFacts, project_money


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


def _money_facts(facts: list[Any], availability: MoneyAvailability) -> list[Any]:
    """Facts whose projected availability matches. Balances and movements stay out."""
    found = []
    seen: set[float] = set()
    for fact in facts:
        view = project_money(getattr(fact, "kind", None))
        if view is None or view.availability != availability:
            continue
        number = fact.value if isinstance(fact.value, (int, float)) else None
        if number is None or float(number) in seen:
            continue
        seen.add(float(number))
        found.append(fact)
    return found


def _committed_reason(excerpt: str) -> str:
    match = re.search(r"committed to\s+(.+)", excerpt or "", flags=re.IGNORECASE)
    if not match:
        return ""
    words = []
    for word in match.group(1).replace(".", " ").split():
        if words and word[:1].isupper():
            break
        words.append(word)
        if len(words) >= 6:
            break
    return " ".join(words).strip(" ,;")


def _source_noun(excerpt: str) -> str:
    match = re.search(r"\bon the\s+([^:.]{1,40})", excerpt or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def render_material_context(facts: list[Any]) -> str:
    """Non-action money that changes what can be invested. No raw quote."""
    received = _money_facts(facts, "available")
    repayments = _money_facts(facts, "unavailable")
    contingent = _money_facts(facts, "contingent")
    if not received and not repayments and not contingent:
        return ""
    sentences = []
    used: set[int] = set()
    for pool in received:
        token = f"{int(pool.value):,}" if float(pool.value) == int(pool.value) else str(pool.value)
        linked = next((item for item in repayments if token in (item.excerpt or "")), None)
        noun = _source_noun(linked.excerpt if linked is not None else "")
        head = f"Of the {format_money(pool.value)}{(' ' + noun) if noun else ''} received"
        if linked is None:
            sentences.append(f"{format_money(pool.value)} is available in connection with the recommendation.")
            continue
        reason = _committed_reason(linked.excerpt or "")
        tail = f"{format_money(linked.value)} is not available to invest"
        if reason:
            tail += f" because it is committed to {reason}"
        sentences.append(f"{head}, {tail}.")
        used.add(id(linked))
    for item in repayments:
        if id(item) in used:
            continue
        sentences.append(f"{format_money(item.value)} is not available to invest.")
    for item in contingent:
        sentences.append(
            f"A further amount of up to {format_money(item.value)} is contingent and is not currently available to invest."
        )
    return " ".join(sentences)


def render_risk_profile_line(facts: list[Any]) -> str:
    """One sentence when a risk profile is known and not in conflict. No suitability rationale."""
    fact = next((item for item in facts if getattr(item, "field", None) == "risk_profile"), None)
    if fact is None or getattr(fact, "conflict", False):
        return ""
    text = str(getattr(fact, "value", "") or "").strip()
    match = re.fullmatch(r"(\d+)\s*(?:\(([^)]+)\))?", text)
    if not match:
        return ""
    number, described = match.group(1), (match.group(2) or "").strip()
    if described:
        return f"Your risk profile is {number}, described as {described}."
    return f"Your risk profile is {number}."


def render_decision_sentences(decisions: list[Any]) -> str:
    """Dispose, retain, and confirm stay out of the action list. They are still part of the advice."""
    sentences = []
    for decision in decisions:
        if getattr(decision, "type", None) not in {"dispose", "retain", "confirm"}:
            continue
        sentence = _decision_sentence(decision)
        if sentence and sentence not in sentences:
            sentences.append(sentence)
    return " ".join(sentences)


def _decision_sentence(decision: Any) -> str:
    subject = str(getattr(decision, "subject", None) or "").strip().rstrip(".")
    kind = decision.type
    if kind == "confirm":
        body = subject or "an outstanding point"
        return f"Still to confirm: {body}."
    if not subject:
        subject = "proceed with a disposal" if kind == "dispose" else "leave the holding unchanged"
    if subject[0].isupper():
        subject = subject[0].lower() + subject[1:]
    return f"We agreed to {subject}."


def render_cgt_statement(_facts: ReconciledFacts, case: CaseDocument | None = None) -> str:
    """Name a supported disposal when one exists. Otherwise keep the generic sentence."""
    tail = (
        "may create a capital gains tax liability, which will be "
        "assessed against the annual exempt amount. [REVIEW: CGT figure]"
    )
    decision = next(
        (
            item
            for item in (case.decisions if case is not None else [])
            if item.type == "dispose" and item.target_account_id and item.amount is None
        ),
        None,
    )
    if decision is None:
        return f"The disposal {tail}"
    account = next(
        (row for row in case.accounts if row.account_id == decision.target_account_id),
        None,
    )
    label = decision.target_account_id
    if account is not None and account.type:
        label = f"{decision.target_account_id} ({account.type})"
    subject = str(getattr(decision, "subject", None) or "").lower()
    head = "A partial disposal" if "portion" in subject or "partial" in subject else "The disposal"
    return f"{head} of {label} {tail}"