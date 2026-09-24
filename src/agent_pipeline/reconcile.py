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


def _group_key(obs: dict[str, Any]) -> tuple[str, str | None]:
    field = str(obs.get("field") or "")
    account_id = obs.get("account_id")
    return field, str(account_id) if account_id else None


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

    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
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

    for (field, account_id), group in sorted(
        groups.items(), key=lambda x: (x[0][0], x[0][1] or "")
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
    }


def write_facts_json(facts: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
