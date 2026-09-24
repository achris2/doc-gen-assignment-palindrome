"""Extract: typed sources → provenance observations (deterministic + LLM)."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI

from agent_pipeline.reconcile import MONEY_KINDS

SourceRole = Literal["request", "meeting", "db", "unknown"]

# Canonical request labels → fact field names
_REQUEST_FIELD_MAP: dict[str, str] = {
    "accounts covered": "accounts_covered",
    "investment amount": "investment_amount",
    "source of funds": "source_of_funds",
    "selling existing investments?": "selling",
    "selling existing investments": "selling",
    "product recommended": "product",
    "held in single or joint name?": "ownership",
    "held in single or joint name": "ownership",
    "agreed risk profile": "risk_profile",
    "initial charge": "initial_charge",
}

_MEETING_EXTRACT_PROMPT = """\
Extract evidence observations from this adviser meeting note for an investment advice report.
Return ONLY valid JSON (no markdown fences):
{{
  "meeting_date": "YYYY-MM-DD or null",
  "observations": [
    {{
      "field": "circumstances|objectives|recommendation_summary|account_value|other descriptive snake_case",
      "value": "string or number as stated",
      "account_id": "MUST be one of the known account ids listed below, or null",
      "as_of": "YYYY-MM-DD if known for this observation, else null",
      "approximate": true,
      "kind": "account_balance|transfer_amount|received_proceeds|loan_repayment|contingent_proceeds|null",
      "quote": "exact sentence from the note supporting this observation"
    }}
  ]
}}

Known accounts (use ONLY these ids for account_id, otherwise null):
{known_accounts}

Rules:
- Capture circumstances, objectives, and the agreed recommendation_summary.
- Capture live/discussed account values as field "account_value".
- account_id MUST be exactly one of the known ids above, or null. Never invent ids
  (no joint_GIA, no free-text account names as ids).
- If the note describes an account without a clear id match, set account_id to null and
  put the description in value; include quote.
- value for account_value should be a number when a figure is stated (strip currency words).
- kind describes what the source number means. Use transfer_amount when money is being moved,
  invested, or withdrawn. Use account_balance only for a stated balance. Use received_proceeds,
  loan_repayment, or contingent_proceeds when those are what the note describes. Never label a
  transfer as account_balance.
- approximate is true when the note uses hedging language (around, about, a little over, etc.).
- Prefer the meeting_date for as_of on meeting figures when a specific date is not given.
- Do not invent fees, tax figures, or amounts not in the note.
- Omit empty observations.
"""


_STRUCTURED_FALLBACK_PROMPT = """\
The source below could not be parsed with the expected schema.
Extract observations for an investment advice report.
Return ONLY valid JSON:
{
  "observations": [
    {
      "field": "snake_case field name",
      "value": "string or number",
      "account_id": null,
      "as_of": null,
      "approximate": false,
      "quote": null
    }
  ]
}
Use fields when present: accounts_covered, investment_amount, source_of_funds, selling,
product, ownership, risk_profile, initial_charge, account_value, circumstances, objectives,
recommendation_summary.
selling must be true/false/yes/no if present. Do not invent values. Never invent account ids.
"""


def observation(
    *,
    field: str,
    value: Any,
    source_role: SourceRole,
    source_file: str,
    as_of: str | None = None,
    account_id: str | None = None,
    quote: str | None = None,
    approximate: bool | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Build one evidence observation."""
    obs: dict[str, Any] = {
        "field": field,
        "value": value,
        "source_role": source_role,
        "source_file": source_file,
    }
    if as_of is not None:
        obs["as_of"] = as_of
    if account_id is not None:
        obs["account_id"] = account_id
    if quote is not None:
        obs["quote"] = quote
    if approximate is not None:
        obs["approximate"] = approximate
    if kind is not None:
        obs["kind"] = kind
    return obs


def parse_request_kv_lines(text: str) -> dict[str, str]:
    """Parse 'Key | Value' lines from a report-request style document."""
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        left, right = line.split("|", 1)
        key = left.strip().lower()
        value = right.strip()
        if key and value:
            pairs[key] = value
    return pairs


def _coerce_selling(raw: str) -> bool | None:
    cleaned = raw.strip().lower()
    if cleaned in {"yes", "y", "true"}:
        return True
    if cleaned in {"no", "n", "false"}:
        return False
    # e.g. "Yes (partial rebalance of the Holloway joint GIA)"
    if cleaned.startswith("yes"):
        return True
    if cleaned.startswith("no"):
        return False
    return None


def extract_request_observations(
    text: str,
    source_file: str,
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministic request extract; LLM fallback if no known keys found."""
    pairs = parse_request_kv_lines(text)
    mapped: dict[str, str] = {}
    for key, value in pairs.items():
        field = _REQUEST_FIELD_MAP.get(key)
        if field:
            mapped[field] = value

    if not mapped:
        return _llm_structured_fallback(
            text,
            source_file=source_file,
            source_role="request",
            openai_client=openai_client,
            model=model,
        )

    out: list[dict[str, Any]] = []
    for field, value in mapped.items():
        if field == "selling":
            coerced = _coerce_selling(value)
            if coerced is None:
                continue
            out.append(
                observation(
                    field="selling",
                    value=coerced,
                    source_role="request",
                    source_file=source_file,
                )
            )
        else:
            kind = "transfer_amount" if field == "investment_amount" else None
            out.append(
                observation(
                    field=field,
                    value=value,
                    source_role="request",
                    source_file=source_file,
                    kind=kind,
                )
            )
    return out


def parse_db_accounts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten holders → unique accounts by account_id (first wins). Preserve nulls."""
    by_id: dict[str, dict[str, Any]] = {}
    holders = data.get("holders") or {}
    if not isinstance(holders, dict):
        return []

    for holder in holders.values():
        if not isinstance(holder, dict):
            continue
        for account in holder.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            account_id = account.get("account_id")
            if not account_id or account_id in by_id:
                continue
            by_id[str(account_id)] = dict(account)
    return list(by_id.values())


def extract_db_observations(
    data: dict[str, Any] | str | Path,
    source_file: str,
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministic DB extract; LLM fallback if holders schema missing."""
    parsed: dict[str, Any] | None
    if isinstance(data, Path):
        try:
            parsed = json.loads(data.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = None
        source_file = data.name
    elif isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = None
    else:
        parsed = data

    if not isinstance(parsed, dict) or "holders" not in parsed:
        text = data if isinstance(data, str) else json.dumps(parsed or {})
        if isinstance(data, Path):
            text = data.read_text(encoding="utf-8") if data.exists() else ""
        return _llm_structured_fallback(
            text,
            source_file=source_file,
            source_role="db",
            openai_client=openai_client,
            model=model,
        )

    snapshot_date = parsed.get("snapshot_date")
    out: list[dict[str, Any]] = []
    if snapshot_date:
        out.append(
            observation(
                field="snapshot_date",
                value=snapshot_date,
                source_role="db",
                source_file=source_file,
                as_of=str(snapshot_date),
            )
        )

    for account in parse_db_accounts(parsed):
        account_id = str(account["account_id"])
        as_of = account.get("valuation_date")
        as_of_str = str(as_of) if as_of else (
            str(snapshot_date) if snapshot_date else None
        )

        for meta_field in ("type", "owner", "platform", "status", "currency"):
            if meta_field in account and account[meta_field] is not None:
                out.append(
                    observation(
                        field=f"account_{meta_field}",
                        value=account[meta_field],
                        source_role="db",
                        source_file=source_file,
                        account_id=account_id,
                        as_of=as_of_str,
                    )
                )

        # Always emit account_value, including explicit null → review later
        out.append(
            observation(
                field="account_value",
                value=account.get("value"),
                source_role="db",
                source_file=source_file,
                account_id=account_id,
                as_of=as_of_str,
                kind="account_balance",
            )
        )
    return out


def _parse_json_object(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _llm_json(
    openai_client: OpenAI,
    model: str,
    prompt: str,
    body: str,
    *,
    temperature: float | None = None,
) -> dict[str, Any] | None:
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": f"{prompt}\n\n---\n\n{body}"}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = openai_client.chat.completions.create(**kwargs)
        raw = response.choices[0].message.content or "{}"
        return _parse_json_object(raw)
    except Exception:
        return None


def _observations_from_llm_payload(
    payload: dict[str, Any],
    *,
    source_role: SourceRole,
    source_file: str,
    default_as_of: str | None = None,
    known_account_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed = known_account_ids
    out: list[dict[str, Any]] = []
    for item in payload.get("observations") or []:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        if not field:
            continue
        value = item.get("value")
        if field == "selling" and isinstance(value, str):
            coerced = _coerce_selling(value)
            if coerced is None:
                continue
            value = coerced
        as_of = item.get("as_of") or default_as_of
        account_id = item.get("account_id")
        if account_id is not None:
            account_id = str(account_id)
            if allowed is not None and account_id not in allowed:
                account_id = None
        quote = item.get("quote")
        approximate = item.get("approximate")
        kind = item.get("kind")
        if kind not in MONEY_KINDS:
            kind = None
        out.append(
            observation(
                field=str(field),
                value=value,
                source_role=source_role,
                source_file=source_file,
                as_of=str(as_of) if as_of else None,
                account_id=account_id,
                quote=str(quote) if quote else None,
                approximate=bool(approximate) if approximate is not None else None,
                kind=kind,
            )
        )
    return out


def _format_known_accounts(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return "(none — leave account_id null)"
    lines = []
    for acc in accounts:
        lines.append(
            f"- {acc.get('account_id')}: type={acc.get('type')!r}, "
            f"platform={acc.get('platform')!r}, owner={acc.get('owner')!r}"
        )
    return "\n".join(lines)


def extract_meeting_observations(
    text: str,
    source_file: str,
    *,
    openai_client: OpenAI,
    model: str,
    known_accounts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One structured LLM extraction over meeting notes."""
    accounts = known_accounts or []
    known_ids = {str(a["account_id"]) for a in accounts if a.get("account_id")}
    prompt = _MEETING_EXTRACT_PROMPT.format(
        known_accounts=_format_known_accounts(accounts)
    )
    payload = _llm_json(openai_client, model, prompt, text, temperature=0)
    if not payload:
        return []
    meeting_date = payload.get("meeting_date")
    default_as_of = str(meeting_date) if meeting_date else None
    return _observations_from_llm_payload(
        payload,
        source_role="meeting",
        source_file=source_file,
        default_as_of=default_as_of,
        known_account_ids=known_ids or None,
    )


def _llm_structured_fallback(
    text: str,
    *,
    source_file: str,
    source_role: SourceRole,
    openai_client: OpenAI | None,
    model: str | None,
) -> list[dict[str, Any]]:
    if openai_client is None or not model:
        return []
    payload = _llm_json(openai_client, model, _STRUCTURED_FALLBACK_PROMPT, text)
    if not payload:
        return []
    return _observations_from_llm_payload(
        payload, source_role=source_role, source_file=source_file
    )


def _known_accounts_from_db_obs(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build known-account list from db observations already extracted."""
    by_id: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if obs.get("source_role") != "db":
            continue
        aid = obs.get("account_id")
        if not aid:
            continue
        aid = str(aid)
        acc = by_id.setdefault(aid, {"account_id": aid})
        field = obs.get("field") or ""
        if field == "account_type":
            acc["type"] = obs.get("value")
        elif field == "account_platform":
            acc["platform"] = obs.get("value")
        elif field == "account_owner":
            acc["owner"] = obs.get("value")
    return list(by_id.values())


def extract_observations(
    typed_sources: dict[str, dict[str, Any]],
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Extract all observations from typed retrieve sources (request/meeting/db)."""
    observations: list[dict[str, Any]] = []

    if "request" in typed_sources:
        src = typed_sources["request"]
        observations.extend(
            extract_request_observations(
                src["text"],
                src.get("name", "request"),
                openai_client=openai_client,
                model=model,
            )
        )

    if "db" in typed_sources:
        src = typed_sources["db"]
        path = src.get("path")
        if isinstance(path, Path) and path.suffix.lower() == ".json":
            observations.extend(
                extract_db_observations(
                    path,
                    src.get("name", path.name),
                    openai_client=openai_client,
                    model=model,
                )
            )
        else:
            observations.extend(
                extract_db_observations(
                    src.get("text", ""),
                    src.get("name", "db"),
                    openai_client=openai_client,
                    model=model,
                )
            )

    if "meeting" in typed_sources and openai_client is not None and model:
        src = typed_sources["meeting"]
        known = _known_accounts_from_db_obs(observations)
        observations.extend(
            extract_meeting_observations(
                src["text"],
                src.get("name", "meeting"),
                openai_client=openai_client,
                model=model,
                known_accounts=known,
            )
        )

    return observations


def is_valid_iso_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False
