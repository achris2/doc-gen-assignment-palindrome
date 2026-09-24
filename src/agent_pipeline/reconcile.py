"""Reconcile: observations → draft facts with conflicts preserved."""

from datetime import date
from pathlib import Path
from typing import Any
import json
import re

# Field-specific preferred authority (non-amount). Amounts use freshness instead.
_REQUEST_FIELDS = frozenset(
    {
        "accounts_covered",
        "investment_amount",
        "source_of_funds",
        "selling",
        "product",
        "ownership",
        "risk_profile",
        "initial_charge",
    }
)
_MEETING_FIELDS = frozenset(
    {"circumstances", "objectives", "recommendation_summary"}
)
_DB_META_FIELDS = frozenset(
    {"account_type", "account_owner", "account_platform", "account_status", "account_currency"}
)

FEE_REVIEW_ITEMS = (
    "[REVIEW: platform fee]",
    "[REVIEW: advice fee]",
)
CGT_REVIEW_ITEM = "[REVIEW: CGT figure]"

MONEY_KINDS = frozenset(
    {
        "account_balance",
        "transfer_amount",
        "received_proceeds",
        "loan_repayment",
        "contingent_proceeds",
    }
)
# Request fields that are amounts, and what those source numbers mean.
_REQUEST_AMOUNT_KIND = {"investment_amount": "transfer_amount"}

# Relative/absolute tolerance for numeric "materially different"
_ABS_TOL = 1.0  # £1
_REL_TOL = 0.001  # 0.1%


def _parse_date(value: str | None) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        cleaned = re.sub(r"(?i)^(gbp|£|usd|\$|eur|€)\s*", "", cleaned).strip()
        # Only treat as numeric if the whole string is a number (optional currency already stripped)
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
    # non-numeric: normalize strings
    sa = "" if a is None else str(a).strip().lower()
    sb = "" if b is None else str(b).strip().lower()
    return sa != sb


def money_kind(obs: dict[str, Any]) -> str | None:
    """What a source number means. Balances stay balances when kind is omitted."""
    kind = obs.get("kind")
    if kind in MONEY_KINDS:
        return str(kind)
    if obs.get("field") == "account_value":
        return "account_balance"
    mapped = _REQUEST_AMOUNT_KIND.get(str(obs.get("field") or ""))
    return mapped


def _group_key(obs: dict[str, Any]) -> tuple[str, str | None, str | None]:
    field = str(obs.get("field") or "")
    account_id = obs.get("account_id")
    return field, str(account_id) if account_id else None, money_kind(obs)


def _preferred_role(field: str) -> str | None:
    if field in _REQUEST_FIELDS:
        return "request"
    if field in _MEETING_FIELDS:
        return "meeting"
    if field in _DB_META_FIELDS or field.startswith("account_"):
        if field == "account_value":
            return None  # freshness policy
        return "db"
    return None


def _pick_by_authority(group: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    preferred = _preferred_role(field)
    if preferred:
        preferred_obs = [o for o in group if o.get("source_role") == preferred]
        if preferred_obs:
            return preferred_obs[0]
    return group[0] if group else None


def _pick_by_freshness(group: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """Return (chosen, resolvable). Undated disagreement → (None, False)."""
    dated = [(o, _parse_date(o.get("as_of"))) for o in group]
    with_dates = [(o, d) for o, d in dated if d is not None]
    if len(with_dates) >= 1 and all(d is not None for _, d in dated):
        chosen = max(with_dates, key=lambda pair: pair[1])[0]
        return chosen, True
    if len(with_dates) == 1 and len(group) == 1:
        return with_dates[0][0], True
    # Some dated, some not, or none dated — only auto-resolve if a unique freshest
    # among dated and undated don't introduce a different value... keep strict:
    if not with_dates:
        return None, False
    # If any undated exists alongside dated, still pick freshest dated for draft
    # but caller always keeps conflict if values differ.
    chosen = max(with_dates, key=lambda pair: pair[1])[0]
    undated = [o for o, d in dated if d is None]
    if undated:
        # Draft pick exists but flag as needing human if undated peers disagree
        return chosen, False
    return chosen, True


def reconcile_observations(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse observations into draft facts; never drop conflicting evidence.

    Only ``db`` observations create account shells. Non-db account figures that
    do not match an existing db id become unmatched REVIEW items (no phantoms).
    """
    # Seed accounts from db only
    accounts: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if obs.get("source_role") != "db":
            continue
        aid = obs.get("account_id")
        if not aid:
            continue
        aid = str(aid)
        accounts.setdefault(aid, {"account_id": aid})

    db_ids = set(accounts.keys())
    review_items: list[str] = []

    # Drop / flag non-db account_value rows that invent ids or lack an id
    filtered: list[dict[str, Any]] = []
    for obs in observations:
        if obs.get("field") != "account_value":
            filtered.append(obs)
            continue
        role = obs.get("source_role")
        aid = obs.get("account_id")
        if role == "db":
            filtered.append(obs)
            continue
        if aid and str(aid) in db_ids:
            filtered.append(obs)
            continue
        # Unmatched non-db figure → REVIEW, do not create account
        quote = obs.get("quote") or obs.get("value")
        review_items.append(f"[REVIEW: unmatched figure: {quote}]")
    observations = filtered

    groups: dict[tuple[str, str | None, str | None], list[dict[str, Any]]] = {}
    for obs in observations:
        if not obs.get("field"):
            continue
        groups.setdefault(_group_key(obs), []).append(obs)

    conflicts: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}

    def _ensure_account(account_id: str) -> dict[str, Any] | None:
        """Only touch accounts that already exist from db."""
        if account_id not in accounts:
            return None
        return accounts[account_id]

    recorded: list[dict[str, Any]] = []

    for (field, account_id, kind), group in sorted(
        groups.items(), key=lambda x: (x[0][0], x[0][1] or "", x[0][2] or "")
    ):
        # Null account values → review, no invention
        if field == "account_value" and any(o.get("value") is None for o in group):
            label = account_id or "unknown account"
            review_items.append(f"[REVIEW: {label} value]")
            if account_id:
                acc = _ensure_account(account_id)
                if acc is not None:
                    null_obs = next(o for o in group if o.get("value") is None)
                    acc["value"] = None
                    acc["value_source"] = null_obs.get("source_role")
                    acc["value_source_file"] = null_obs.get("source_file")
                    acc["as_of"] = null_obs.get("as_of")
                    acc["needs_review"] = True
            group = [o for o in group if o.get("value") is not None]
            if not group:
                continue

        unique_vals: list[Any] = []
        for o in group:
            v = o.get("value")
            if not any(not values_materially_differ(v, u) for u in unique_vals):
                unique_vals.append(v)

        has_conflict = len(unique_vals) > 1
        if has_conflict:
            detail_parts = []
            for o in group:
                bit = (
                    f"{o.get('source_role')}@{o.get('as_of') or 'undated'}="
                    f"{o.get('value')!r}"
                )
                if o.get("source_file"):
                    bit += f" ({o.get('source_file')})"
                if o.get("quote"):
                    bit += f' quote="{o.get("quote")}"'
                detail_parts.append(bit)
            conflict_field = f"{field}:{account_id}" if account_id else field
            conflicts.append(
                {
                    "field": conflict_field,
                    "details": "; ".join(detail_parts),
                    "observations": group,
                }
            )

        if field == "account_value":
            chosen, resolvable = _pick_by_freshness(group)
            if has_conflict and not resolvable:
                review_items.append(
                    f"[REVIEW: {account_id or field} conflict — undated/unresolvable]"
                )
                draft = None
                if account_id:
                    acc = _ensure_account(account_id)
                    if acc is not None:
                        acc["value"] = None
                        acc["value_conflict"] = True
                        acc["needs_review"] = True
                        acc["value_alternates"] = [
                            {
                                "value": o.get("value"),
                                "source_role": o.get("source_role"),
                                "source_file": o.get("source_file"),
                                "as_of": o.get("as_of"),
                                "quote": o.get("quote"),
                            }
                            for o in group
                        ]
            else:
                draft = chosen
        else:
            draft = _pick_by_authority(group, field)
            if has_conflict:
                review_items.append(f"[REVIEW: {field} conflict]")

        if draft is None:
            continue

        entry = {
            "value": draft.get("value"),
            "source": draft.get("source_role"),
            "source_file": draft.get("source_file"),
            "as_of": draft.get("as_of"),
            "account_id": account_id,
            "conflict": has_conflict,
        }

        recorded.append(
            {
                "field": field,
                "account_id": account_id,
                "kind": kind,
                "draft": draft,
                "group": group,
                "conflict": has_conflict,
            }
        )

        if (
            account_id
            and field == "account_value"
            and kind not in (None, "account_balance")
        ):
            facts[f"{kind}:{account_id}"] = {**entry, "kind": kind}
            continue

        if account_id and field.startswith("account_"):
            acc = _ensure_account(account_id)
            if acc is None:
                continue
            if field == "account_value":
                acc["value"] = entry["value"]
                acc["value_source"] = entry["source"]
                acc["value_source_file"] = entry["source_file"]
                acc["as_of"] = entry["as_of"]
                acc["value_conflict"] = has_conflict
                if draft.get("approximate"):
                    acc["approximate"] = True
                    acc["needs_review"] = True
                if has_conflict:
                    acc["needs_review"] = True
                    acc["value_alternates"] = [
                        {
                            "value": o.get("value"),
                            "source_role": o.get("source_role"),
                            "source_file": o.get("source_file"),
                            "as_of": o.get("as_of"),
                            "quote": o.get("quote"),
                        }
                        for o in group
                        if values_materially_differ(o.get("value"), entry["value"])
                        or o.get("source_role") != entry["source"]
                    ]
            else:
                short = field.removeprefix("account_")
                acc[short] = entry["value"]
                acc[f"{short}_source"] = entry["source"]
        elif field == "snapshot_date":
            facts["snapshot_date"] = entry
        else:
            facts[field] = {
                "value": entry["value"],
                "source": entry["source"],
                "source_file": entry["source_file"],
                "as_of": entry["as_of"],
                "conflict": has_conflict,
            }

    selling = False
    if "selling" in facts:
        selling = bool(facts["selling"]["value"])

    for item in FEE_REVIEW_ITEMS:
        if item not in review_items:
            review_items.append(item)
    if selling and CGT_REVIEW_ITEM not in review_items:
        review_items.append(CGT_REVIEW_ITEM)

    seen: set[str] = set()
    deduped_review: list[str] = []
    for item in review_items:
        if item not in seen:
            seen.add(item)
            deduped_review.append(item)

    return {
        "selling": selling,
        "conflicts": [
            {"field": c["field"], "details": c["details"]} for c in conflicts
        ],
        "review_items": deduped_review,
        "facts": facts,
        "accounts": sorted(accounts.values(), key=lambda a: a["account_id"]),
        "observation_count": len(observations),
        "recorded": recorded,
    }


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or "unknown"


def excerpt_for(obs: dict[str, Any], *, kind: str | None = None) -> str:
    """Prose sources keep the quote. Structured sources keep field and value."""
    quote = obs.get("quote")
    if quote:
        return str(quote)
    label = kind or obs.get("field") or "field"
    account_id = obs.get("account_id")
    prefix = f"{label} {account_id}" if account_id else str(label)
    return f"{prefix} = {obs.get('value')}"


def fact_id_for(field: str, account_id: str | None, kind: str | None) -> str:
    if account_id and kind:
        return f"f-account-{account_id}-{kind}"
    if account_id:
        return f"f-account-{account_id}-{field}"
    return f"f-{field}"


def evidence_rows(group: list[dict[str, Any]], kind: str | None) -> list[dict[str, Any]]:
    rows = []
    for obs in group:
        rows.append(
            {
                "source_file": obs.get("source_file"),
                "source_role": obs.get("source_role"),
                "value": obs.get("value"),
                "kind": money_kind(obs) or kind,
                "excerpt": excerpt_for(obs, kind=money_kind(obs) or kind),
            }
        )
    return rows


def case_fact_from_recorded(item: dict[str, Any]) -> dict[str, Any]:
    draft = item["draft"]
    kind = item.get("kind")
    field = item["field"]
    account_id = item.get("account_id")
    fact_id = fact_id_for(field, account_id, kind if field == "account_value" or kind else None)
    if field != "account_value" and kind:
        fact_id = f"f-{field}"
    return {
        "id": fact_id,
        "field": field,
        "value": draft.get("value"),
        "source_file": draft.get("source_file"),
        "excerpt": excerpt_for(draft, kind=kind),
        "conflict": bool(item.get("conflict")),
        "kind": kind,
        "account_id": account_id,
        "evidence": evidence_rows(item.get("group") or [], kind),
    }


def action_id_for(action_type: str, owner_or_product: str) -> str:
    return f"a-{_slug(action_type)}-{_slug(owner_or_product)}"


def build_actions(case_facts: list[dict[str, Any]], reconciled: dict[str, Any]) -> list[dict[str, Any]]:
    """One joined action per non-balance money fact. Amount is not a kind."""
    by_id = {fact["id"]: fact for fact in case_facts}
    facts = reconciled.get("facts") or {}
    product = (facts.get("product") or {}).get("value")
    owner = (facts.get("ownership") or {}).get("value")
    source = (facts.get("source_of_funds") or {}).get("value")
    summary = (facts.get("recommendation_summary") or {}).get("value")
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(fact: dict[str, Any], action_type: str, who: str) -> None:
        action_id = action_id_for(action_type, who)
        if action_id in seen:
            action_id = f"{action_id}-{_slug(fact['id'])}"
        seen.add(action_id)
        actions.append(
            {
                "id": action_id,
                "amount": fact.get("value"),
                "supports": fact["id"],
                "who": who,
                "product": product,
                "source_of_funds": source,
                "summary": summary,
            }
        )

    for fact in case_facts:
        kind = fact.get("kind")
        if kind in MONEY_KINDS and kind != "account_balance":
            who = str(product or fact.get("account_id") or owner or "client")
            add(fact, "fund" if product else "move", who)
    if not actions and "f-investment_amount" in by_id:
        add(by_id["f-investment_amount"], "fund", str(product or owner or "client"))
    return _collapse_unambiguous_request_amount(actions, by_id)


def _collapse_unambiguous_request_amount(
    actions: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop a request investment_amount when exactly one transfer has the same amount.

    Identity is unambiguous only in that case. An unparsable amount, or two
    other transfers with the same number, leaves every action in place.
    The kept action is the movement, not the request instruction. ``supports``
    stays that fact id. ``corroborated`` records the request fact id.
    """
    drop: set[int] = set()
    for index, action in enumerate(actions):
        fact = by_id.get(action.get("supports")) or {}
        if fact.get("field") != "investment_amount":
            continue
        amount = _to_number(action.get("amount"))
        if amount is None:
            continue
        matches = [
            other_index
            for other_index, other in enumerate(actions)
            if other_index != index
            and (by_id.get(other.get("supports")) or {}).get("field") != "investment_amount"
            and (by_id.get(other.get("supports")) or {}).get("kind") == "transfer_amount"
            and _to_number(other.get("amount")) is not None
            and not values_materially_differ(amount, other.get("amount"))
        ]
        if len(matches) != 1:
            continue
        actions[matches[0]]["corroborated"] = action.get("supports")
        drop.add(index)
    return [action for index, action in enumerate(actions) if index not in drop]


def sources_from_classifications(classifications: dict[str, str]) -> list[dict[str, str]]:
    image = {".png", ".jpg", ".jpeg"}
    rows = []
    for name, role in sorted(classifications.items()):
        status = "skipped" if Path(name).suffix.lower() in image else "parsed"
        rows.append({"file": name, "role": role, "status": status})
    return rows


def build_case_document(
    reconciled: dict[str, Any],
    classifications: dict[str, str] | None = None,
    *,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One case file: sources, typed facts with evidence, and joined actions."""
    case_facts = [
        case_fact_from_recorded(item)
        for item in reconciled.get("recorded") or []
        if item.get("draft") is not None
    ]
    return {
        "run": run or {},
        "sources": sources_from_classifications(classifications or {}),
        "facts": case_facts,
        "actions": build_actions(case_facts, reconciled),
        "sections": {},
        "selling": reconciled.get("selling"),
        "conflicts": reconciled.get("conflicts") or [],
        "review_items": reconciled.get("review_items") or [],
        "accounts": reconciled.get("accounts") or [],
    }


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
    payload: dict[str, Any], actions: list[dict[str, Any]]
) -> tuple[str, list[dict[str, str]]]:
    """One item per action. Amounts are checked against that action only."""
    by_id = {action["id"]: action for action in actions}
    supplied = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    used: dict[str, dict[str, Any]] = {}
    reviews: list[str] = []
    for item in supplied:
        action_id = str(item.get("action_id") or "")
        if action_id not in by_id:
            reviews.append(f"[REVIEW: {action_id or 'unknown'} uncited]")
            continue
        if action_id in used:
            reviews.append(f"[REVIEW: {action_id} uncited]")
            continue
        text = str(item.get("text") or "").strip()
        if not amounts_match_action(text, by_id[action_id].get("amount")):
            reviews.append(f"[REVIEW: {action_id} uncited]")
            continue
        used[action_id] = {"action_id": action_id, "text": text}
    lines: list[str] = []
    stored: list[dict[str, str]] = []
    for action in actions:
        action_id = action["id"]
        if action_id not in used:
            reviews.append(f"[REVIEW: {action_id} uncited]")
            lines.append(f"[REVIEW: {action_id} uncited]")
            continue
        lines.append(used[action_id]["text"])
        stored.append(used[action_id])
    return "\n".join(lines), stored


def write_facts_json(facts: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
