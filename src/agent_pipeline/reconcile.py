"""Reconcile: observations → draft facts with conflicts preserved."""

from datetime import date
from pathlib import Path
from typing import Any
import json
import re

from agent_pipeline.schema import (
    Decision,
    DB_META_FIELDS,
    MEETING_FIELDS,
    MONEY_KINDS,
    REQUEST_FIELDS,
    Account,
    CaseDocument,
    CaseFact,
    Conflict,
    Evidence,
    FileRole,
    MoneyKind,
    Observation,
    ParseStatus,
    MeetingDecision,
    RecommendationAction,
    RecommendationDraft,
    RecommendationItem,
    RecordedFact,
    ReconciledFacts,
    RunHeader,
    SourceRole,
    SourcedValue,
    SourceRecord,
    ValueAlternate,
)

FEE_REVIEW_ITEMS = (
    "[REVIEW: platform fee]",
    "[REVIEW: advice fee]",
)
CGT_REVIEW_ITEM = "[REVIEW: CGT figure]"

_ABS_TOL = 1.0
_REL_TOL = 0.001


def _parse_date(value: str | None) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        cleaned = re.sub(r"(?i)^(gbp|£|usd|\$|eur|€)\s*", "", cleaned).strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def values_materially_differ(a: Any, b: Any) -> bool:
    """True if two values disagree enough to count as a conflict."""
    na, nb = _to_number(a), _to_number(b)
    if na is not None and nb is not None:
        if abs(na - nb) <= _ABS_TOL:
            return False
        scale = max(abs(na), abs(nb), 1.0)
        return abs(na - nb) / scale > _REL_TOL
    sa = "" if a is None else str(a).strip().lower()
    sb = "" if b is None else str(b).strip().lower()
    return sa != sb


def _preferred_role(field: str) -> SourceRole | None:
    if field in REQUEST_FIELDS:
        return "request"
    if field in MEETING_FIELDS:
        return "meeting"
    if field in DB_META_FIELDS or field.startswith("account_"):
        if field == "account_value":
            return None
        return "db"
    return None


def _pick_by_authority(group: list[Observation], field: str) -> Observation | None:
    preferred = _preferred_role(field)
    if preferred:
        preferred_obs = [obs for obs in group if obs.source_role == preferred]
        if preferred_obs:
            return preferred_obs[0]
    return group[0] if group else None


def _pick_by_freshness(group: list[Observation]) -> tuple[Observation | None, bool]:
    """Return (chosen, resolvable). Undated disagreement → (None, False)."""
    dated = [(obs, _parse_date(obs.as_of)) for obs in group]
    with_dates = [(obs, when) for obs, when in dated if when is not None]
    if len(with_dates) >= 1 and all(when is not None for _, when in dated):
        chosen = max(with_dates, key=lambda pair: pair[1])[0]
        return chosen, True
    if len(with_dates) == 1 and len(group) == 1:
        return with_dates[0][0], True
    if not with_dates:
        return None, False
    chosen = max(with_dates, key=lambda pair: pair[1])[0]
    undated = [obs for obs, when in dated if when is None]
    if undated:
        return chosen, False
    return chosen, True


def _alternates(group: list[Observation], chosen: SourcedValue | None = None) -> list[ValueAlternate]:
    rows = []
    for obs in group:
        if chosen is not None:
            same_value = not values_materially_differ(obs.value, chosen.value)
            same_source = obs.source_role == chosen.source
            if same_value and same_source:
                continue
        rows.append(
            ValueAlternate(
                value=obs.value,
                source_role=obs.source_role,
                source_file=obs.source_file,
                as_of=obs.as_of,
                quote=obs.quote,
            )
        )
    return rows


def _conflict_details(group: list[Observation]) -> str:
    parts = []
    for obs in group:
        bit = f"{obs.source_role}@{obs.as_of or 'undated'}={obs.value!r}"
        if obs.source_file:
            bit += f" ({obs.source_file})"
        if obs.quote:
            bit += f' quote="{obs.quote}"'
        parts.append(bit)
    return "; ".join(parts)


def _sourced(
    draft: Observation, account_id: str | None, conflict: bool, kind: MoneyKind | None = None
) -> SourcedValue:
    return SourcedValue(
        value=draft.value,
        source=draft.source_role,
        source_file=draft.source_file,
        as_of=draft.as_of,
        account_id=account_id,
        conflict=conflict,
        kind=kind,
    )


def reconcile_observations(observations: list[Observation]) -> ReconciledFacts:
    """Collapse observations into draft facts; never drop conflicting evidence.

    Only ``db`` observations create account shells. A figure with no custody id
    is kept as a numeric fact and does not create an account.
    """
    accounts: dict[str, Account] = {}
    for obs in observations:
        if obs.source_role != "db" or not obs.account_id:
            continue
        accounts.setdefault(obs.account_id, Account(account_id=obs.account_id))

    db_ids = set(accounts)
    review_items: list[str] = []
    filtered: list[Observation] = []
    for obs in observations:
        if obs.field != "account_value":
            filtered.append(obs)
            continue
        if obs.source_role == "db" or (obs.account_id and obs.account_id in db_ids):
            filtered.append(obs)
            continue
        filtered.extend(_unscoped_money(obs))

    groups: dict[tuple[str, str | None, str | None], list[Observation]] = {}
    for obs in filtered:
        if not obs.field:
            continue
        groups.setdefault(obs.group_key(), []).append(obs)

    conflicts: list[Conflict] = []
    facts: dict[str, SourcedValue] = {}
    recorded: list[RecordedFact] = []

    for (field, account_id, kind), group in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or "")
    ):
        if field == "account_value" and any(obs.value is None for obs in group):
            review_items.append(f"[REVIEW: {account_id or 'unknown account'} value]")
            if account_id and account_id in accounts:
                null_obs = next(obs for obs in group if obs.value is None)
                accounts[account_id].set_value(
                    value=None,
                    source=null_obs.source_role,
                    source_file=null_obs.source_file,
                    as_of=null_obs.as_of,
                    needs_review=True,
                )
            group = [obs for obs in group if obs.value is not None]
            if not group:
                continue

        unique_vals: list[Any] = []
        distinct: list[Observation] = []
        for obs in group:
            if not any(not values_materially_differ(obs.value, seen) for seen in unique_vals):
                unique_vals.append(obs.value)
                distinct.append(obs)
        has_conflict = len(unique_vals) > 1
        if field in MEETING_FIELDS and _same_source_narrative(group) and len(distinct) > 1:
            _record_narrative_group(distinct, field, account_id, kind, recorded, facts)
            continue
        if has_conflict:
            label = f"{field}:{account_id}" if account_id else field
            conflicts.append(Conflict(field=label, details=_conflict_details(group), observations=group))

        draft: Observation | None
        if field == "account_value":
            chosen, resolvable = _pick_by_freshness(group)
            if has_conflict and not resolvable:
                review_items.append(f"[REVIEW: {account_id or field} conflict — undated/unresolvable]")
                if account_id and account_id in accounts:
                    accounts[account_id].set_value(
                        value=None,
                        source=None,
                        source_file=None,
                        as_of=None,
                        conflict=True,
                        needs_review=True,
                        alternates=_alternates(group),
                    )
                continue
            draft = chosen
        else:
            draft = _pick_by_authority(group, field)
            if has_conflict:
                review_items.append(f"[REVIEW: {field} conflict]")

        if draft is None:
            continue

        entry = _sourced(draft, account_id, has_conflict, kind)
        recorded.append(
            RecordedFact(
                field=field,
                account_id=account_id,
                kind=kind,
                draft=draft,
                group=group,
                conflict=has_conflict,
            )
        )

        if account_id and field == "account_value" and kind not in (None, "account_balance"):
            facts[f"{kind}:{account_id}"] = entry
            continue

        if account_id and field.startswith("account_"):
            account = accounts.get(account_id)
            if account is None:
                continue
            if field == "account_value":
                alternates = _alternates(group, entry) if has_conflict else []
                account.set_value(
                    value=entry.value,
                    source=entry.source,
                    source_file=entry.source_file,
                    as_of=entry.as_of,
                    conflict=has_conflict,
                    approximate=bool(draft.approximate),
                    needs_review=has_conflict or bool(draft.approximate),
                    alternates=alternates or None,
                )
            else:
                account.set_meta(field.removeprefix("account_"), entry.value, entry.source)
        elif field == "snapshot_date":
            facts["snapshot_date"] = entry
        else:
            facts[field] = SourcedValue(
                value=entry.value,
                source=entry.source,
                source_file=entry.source_file,
                as_of=entry.as_of,
                conflict=has_conflict,
            )

    selling = bool(facts["selling"].value) if "selling" in facts else False
    for item in FEE_REVIEW_ITEMS:
        if item not in review_items:
            review_items.append(item)
    if selling and CGT_REVIEW_ITEM not in review_items:
        review_items.append(CGT_REVIEW_ITEM)

    seen: set[str] = set()
    deduped = []
    for item in review_items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return ReconciledFacts(
        selling=selling,
        conflicts=conflicts,
        review_items=deduped,
        facts=facts,
        accounts=sorted(accounts.values(), key=lambda account: account.account_id),
        observation_count=len(filtered),
        recorded=recorded,
    )


def _same_source_narrative(group: list[Observation]) -> bool:
    """Several sentences from one meeting are not a disagreement."""
    roles = {obs.source_role for obs in group}
    return len(roles) == 1


def _record_narrative_group(
    distinct: list[Observation],
    field: str,
    account_id: str | None,
    kind: MoneyKind | None,
    recorded: list[RecordedFact],
    facts: dict[str, SourcedValue],
) -> None:
    for index, draft in enumerate(distinct):
        entry = _sourced(draft, account_id, False, kind)
        recorded.append(
            RecordedFact(
                field=field,
                account_id=account_id,
                kind=kind,
                draft=draft,
                group=[draft],
                conflict=False,
            )
        )
        key = field if index == 0 else f"{field}__{index + 1}"
        facts[key] = SourcedValue(
            value=entry.value,
            source=entry.source,
            source_file=entry.source_file,
            as_of=entry.as_of,
            conflict=False,
        )


def _assign_unique_fact_ids(case_facts: list[CaseFact]) -> None:
    seen: dict[str, int] = {}
    for fact in case_facts:
        seen[fact.id] = seen.get(fact.id, 0) + 1
        if seen[fact.id] > 1:
            fact.id = f"{fact.id}-{seen[fact.id]}"


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or "unknown"


def excerpt_for(obs: Observation, *, kind: MoneyKind | None = None) -> str:
    if obs.quote:
        return obs.quote
    label = kind or obs.field or "field"
    prefix = f"{label} {obs.account_id}" if obs.account_id else str(label)
    return f"{prefix} = {obs.value}"


def fact_id_for(field: str, account_id: str | None, kind: MoneyKind | None) -> str:
    if account_id and kind:
        return f"f-account-{account_id}-{kind}"
    if account_id:
        return f"f-account-{account_id}-{field}"
    return f"f-{field}"


def evidence_rows(group: list[Observation], kind: MoneyKind | None) -> list[Evidence]:
    rows = []
    for obs in group:
        row_kind = obs.money_kind() or kind
        rows.append(
            Evidence(
                source_file=obs.source_file,
                source_role=obs.source_role,
                value=obs.value,
                kind=row_kind,
                excerpt=excerpt_for(obs, kind=row_kind),
            )
        )
    return rows


def case_fact_from_recorded(item: RecordedFact) -> CaseFact:
    kind = item.kind
    fact_id = fact_id_for(
        item.field,
        item.account_id,
        kind if item.field == "account_value" or kind else None,
    )
    if item.field != "account_value" and kind:
        fact_id = f"f-{item.field}"
    return CaseFact(
        id=fact_id,
        field=item.field,
        value=item.draft.value,
        source_file=item.draft.source_file,
        excerpt=excerpt_for(item.draft, kind=kind),
        conflict=item.conflict,
        kind=kind,
        account_id=item.account_id,
        evidence=evidence_rows(item.group, kind),
    )


def action_id_for(action_type: str, owner_or_product: str) -> str:
    return f"a-{_slug(action_type)}-{_slug(owner_or_product)}"


_ACTION_KINDS = frozenset({"transfer_amount", "received_proceeds", "loan_repayment"})
_KEPT_KINDS = _ACTION_KINDS | {"contingent_proceeds"}


def _distinct_amounts(obs: Observation) -> list[float]:
    found: list[float] = []
    number = _to_number(obs.value)
    if number is not None:
        found.append(number)
    for amount in pound_amounts(f"{obs.value or ''} {obs.quote or ''}"):
        if all(values_materially_differ(amount, seen) for seen in found):
            found.append(amount)
    return found


def _unscoped_money(obs: Observation) -> list[Observation]:
    """One numeric fact per amount. A kind applies only to the observation's own number."""
    kind = obs.kind if obs.kind in _KEPT_KINDS else None
    stated = _to_number(obs.value)
    kept = []
    for amount in _distinct_amounts(obs):
        amount_kind = kind if stated is not None and not values_materially_differ(amount, stated) else None
        slug = str(int(amount)) if amount == int(amount) else str(amount)
        field = f"{amount_kind or 'money_amount'}_{slug}"
        kept.append(
            Observation(
                field=field,
                value=amount,
                source_role=obs.source_role,
                source_file=obs.source_file,
                as_of=obs.as_of,
                account_id=None,
                quote=obs.quote,
                approximate=obs.approximate,
                kind=amount_kind,
            )
        )
    return kept


def build_actions(case_facts: list[CaseFact], reconciled: ReconciledFacts) -> list[RecommendationAction]:
    """One action per numeric transfer. A receipt or repayment stays a fact."""
    by_id = {fact.id: fact for fact in case_facts}
    product = reconciled.fact_value("product", default=None)
    owner = reconciled.fact_value("ownership", default=None)
    source = reconciled.fact_value("source_of_funds", default=None)
    summary = reconciled.fact_value("recommendation_summary", default=None)
    actions: list[RecommendationAction] = []
    seen: set[str] = set()

    def add(fact: CaseFact, action_type: str, who: str) -> None:
        action_id = action_id_for(action_type, who)
        if action_id in seen:
            action_id = f"{action_id}-{_slug(fact.id)}"
        seen.add(action_id)
        actions.append(
            RecommendationAction(
                id=action_id,
                amount=fact.value,
                supports=fact.id,
                who=who,
                product=product,
                source_of_funds=source,
                summary=summary,
            )
        )

    for fact in case_facts:
        if fact.kind != "transfer_amount" or _to_number(fact.value) is None:
            continue
        who = str(fact.account_id or product or owner or "client")
        add(fact, "fund" if product else "move", who)
    actions = _collapse_unambiguous_request_amount(actions, by_id)
    if actions:
        return actions
    summary_fact = next((fact for fact in case_facts if fact.field == "recommendation_summary"), None)
    if summary_fact is None or not str(summary_fact.value or "").strip() or summary_fact.kind:
        return actions
    actions.append(
        RecommendationAction(
            id=action_id_for("contribute", "not-agreed"),
            amount=None,
            supports=summary_fact.id,
            who=str(product or owner or "client"),
            product=product,
            source_of_funds=source,
            summary=summary_fact.value,
            amount_status="not_agreed",
        )
    )
    return actions


def _collapse_unambiguous_request_amount(
    actions: list[RecommendationAction], by_id: dict[str, CaseFact]
) -> list[RecommendationAction]:
    """Drop a request investment_amount when exactly one transfer has the same amount."""
    drop: set[int] = set()
    for index, action in enumerate(actions):
        fact = by_id.get(action.supports)
        if fact is None or fact.field != "investment_amount":
            continue
        amount = _to_number(action.amount)
        if amount is None:
            continue
        matches = []
        for other_index, other in enumerate(actions):
            if other_index == index:
                continue
            other_fact = by_id.get(other.supports)
            if other_fact is None or other_fact.field == "investment_amount":
                continue
            if other_fact.kind != "transfer_amount":
                continue
            if _to_number(other.amount) is None:
                continue
            if values_materially_differ(amount, other.amount):
                continue
            matches.append(other_index)
        if len(matches) != 1:
            continue
        actions[matches[0]].corroborated = action.supports
        drop.add(index)
    return [action for index, action in enumerate(actions) if index not in drop]


def meeting_decision_records(
    meeting_decisions: list[MeetingDecision],
) -> tuple[list[CaseFact], list[Decision]]:
    """Store dispose, retain, and confirm as facts plus decisions. Never as actions."""
    facts: list[CaseFact] = []
    decisions: list[Decision] = []
    seen: set[str] = set()
    for item in meeting_decisions:
        if item.type not in {"dispose", "retain", "confirm"}:
            continue
        quote = item.quote.strip()
        if not quote or quote in seen:
            continue
        seen.add(quote)
        slug = _slug(item.target_account_id or item.subject or item.type)
        fact_id = f"f-{item.type}-{slug}"
        decision_id = f"d-{item.type}-{slug}"
        suffix = 2
        while fact_id in {fact.id for fact in facts} or decision_id in {row.id for row in decisions}:
            fact_id = f"f-{item.type}-{slug}-{suffix}"
            decision_id = f"d-{item.type}-{slug}-{suffix}"
            suffix += 1
        facts.append(
            CaseFact(
                id=fact_id,
                field=item.type,
                value=item.subject or quote,
                source_file=item.source_file,
                excerpt=quote,
                conflict=False,
                kind=None,
                account_id=item.target_account_id,
                evidence=[
                    Evidence(
                        source_file=item.source_file,
                        source_role="meeting",
                        value=item.subject or quote,
                        kind=None,
                        excerpt=quote,
                    )
                ],
            )
        )
        decisions.append(
            Decision(
                id=decision_id,
                type=item.type,
                status=item.status,
                supports=fact_id,
                target_account_id=item.target_account_id,
                subject=item.subject,
                amount=None,
            )
        )
    return facts, decisions


def build_decisions(
    actions: list[RecommendationAction], case_facts: list[CaseFact]
) -> list[Decision]:
    """Copy today's actions. A numeric transfer stays one decision and blocks a summary decision."""
    by_id = {fact.id: fact for fact in case_facts}
    decisions: list[Decision] = []
    for action in actions:
        fact = by_id.get(action.supports)
        if fact is not None and fact.kind == "transfer_amount" and _to_number(action.amount) is not None:
            decisions.append(
                Decision(
                    id=f"d-transfer-{_slug(fact.id)}",
                    type="transfer",
                    status="agreed",
                    supports=action.supports,
                    subject=None if action.product in (None, "") else str(action.product),
                    amount=action.amount,
                )
            )
            continue
        if action.amount is None and action.amount_status == "not_agreed":
            decisions.append(
                Decision(
                    id="d-contribute-not-agreed",
                    type="contribute",
                    status="agreed",
                    supports=action.supports,
                    subject=None if not str(action.summary or "").strip() else str(action.summary),
                    amount=None,
                    amount_status="not_agreed",
                )
            )
    return decisions


def sources_from_classifications(classifications: dict[str, FileRole]) -> list[SourceRecord]:
    image = {".png", ".jpg", ".jpeg"}
    rows = []
    for name, role in sorted(classifications.items()):
        status: ParseStatus = "skipped" if Path(name).suffix.lower() in image else "parsed"
        rows.append(SourceRecord(file=name, role=role, status=status))
    return rows


def build_case_document(
    reconciled: ReconciledFacts,
    classifications: dict[str, FileRole] | None = None,
    *,
    run: RunHeader | None = None,
    meeting_decisions: list[MeetingDecision] | None = None,
) -> CaseDocument:
    """One case file: sources, typed facts with evidence, and joined actions."""
    case_facts = [case_fact_from_recorded(item) for item in reconciled.recorded]
    _assign_unique_fact_ids(case_facts)
    actions = build_actions(case_facts, reconciled)
    extra_facts, extra_decisions = meeting_decision_records(meeting_decisions or [])
    case_facts.extend(extra_facts)
    return CaseDocument(
        run=run,
        sources=sources_from_classifications(classifications or {}),
        facts=case_facts,
        actions=actions,
        decisions=build_decisions(actions, case_facts) + extra_decisions,
        sections={},
        selling=reconciled.selling,
        conflicts=list(reconciled.conflicts),
        review_items=list(reconciled.review_items),
        accounts=list(reconciled.accounts),
    )


def pound_amounts(text: str) -> list[float]:
    found = []
    for match in re.findall(r"£\s*([\d,]+(?:\.\d+)?)", text or ""):
        number = _to_number(match)
        if number is not None:
            found.append(number)
    return found


def amounts_match_action(text: str, amount: Any) -> bool:
    """True when every £ figure in text is this action's amount. No £ is ok."""
    found = pound_amounts(text)
    if not found:
        return True
    target = _to_number(amount)
    if target is None:
        return False
    return all(not values_materially_differ(number, target) for number in found)


def validate_recommendation_items(
    draft: RecommendationDraft, actions: list[RecommendationAction]
) -> tuple[str, list[RecommendationItem]]:
    """One item per action. Amounts are checked against that action only."""
    by_id = {action.id: action for action in actions}
    used: dict[str, RecommendationItem] = {}
    reviews: list[str] = []
    for item in draft.items:
        if item.action_id not in by_id or item.action_id in used:
            reviews.append(f"[REVIEW: {item.action_id or 'unknown'} uncited]")
            continue
        if not amounts_match_action(item.text, by_id[item.action_id].amount):
            reviews.append(f"[REVIEW: {item.action_id} uncited]")
            continue
        used[item.action_id] = item
    lines: list[str] = []
    stored: list[RecommendationItem] = []
    for action in actions:
        if action.id not in used:
            reviews.append(f"[REVIEW: {action.id} uncited]")
            lines.append(f"[REVIEW: {action.id} uncited]")
            continue
        lines.append(used[action.id].text)
        stored.append(used[action.id])
    return "\n".join(lines), stored


def write_facts_json(case: CaseDocument, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
