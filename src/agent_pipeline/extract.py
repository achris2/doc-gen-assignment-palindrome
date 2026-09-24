"""Extract: typed sources → provenance observations (deterministic + LLM)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent_pipeline.llm import JsonChat
from agent_pipeline.schema import (
    MONEY_KINDS,
    DbAccount,
    KnownAccount,
    MeetingExtract,
    Observation,
    SourceRole,
    TypedSource,
)

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
) -> Observation:
    return Observation(
        field=field,
        value=value,
        source_role=source_role,
        source_file=source_file,
        as_of=as_of,
        account_id=account_id,
        quote=quote,
        approximate=approximate,
        kind=kind,
    )


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
) -> list[Observation]:
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

    out: list[Observation] = []
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


def parse_db_accounts(data: dict[str, Any]) -> list[DbAccount]:
    """Flatten holders → unique accounts by account_id (first wins). Preserve nulls."""
    by_id: dict[str, DbAccount] = {}
    holders = data.get("holders") or {}
    if not isinstance(holders, dict):
        return []

    for holder in holders.values():
        if not isinstance(holder, dict):
            continue
        for account in holder.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            parsed = DbAccount.from_dict(account)
            if parsed is None or parsed.account_id in by_id:
                continue
            by_id[parsed.account_id] = parsed
    return list(by_id.values())


def _load_db_payload(data: dict[str, Any] | str | Path) -> tuple[dict[str, Any] | None, str]:
    if isinstance(data, Path):
        try:
            parsed = json.loads(data.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = None
        text = data.read_text(encoding="utf-8") if data.exists() else ""
        return parsed if isinstance(parsed, dict) else None, text
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = None
        return parsed if isinstance(parsed, dict) else None, data
    return data, json.dumps(data)


def extract_db_observations(
    data: dict[str, Any] | str | Path,
    source_file: str,
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> list[Observation]:
    """Deterministic DB extract; LLM fallback if holders schema missing."""
    parsed, text = _load_db_payload(data)
    if isinstance(data, Path):
        source_file = data.name

    if not isinstance(parsed, dict) or "holders" not in parsed:
        return _llm_structured_fallback(
            text,
            source_file=source_file,
            source_role="db",
            openai_client=openai_client,
            model=model,
        )

    snapshot_date = parsed.get("snapshot_date")
    out: list[Observation] = []
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
        as_of = account.valuation_date
        as_of_str = str(as_of) if as_of else (str(snapshot_date) if snapshot_date else None)
        meta = {
            "type": account.type,
            "owner": account.owner,
            "platform": account.platform,
            "status": account.status,
            "currency": account.currency,
        }
        for meta_field, meta_value in meta.items():
            if meta_value is not None:
                out.append(
                    observation(
                        field=f"account_{meta_field}",
                        value=meta_value,
                        source_role="db",
                        source_file=source_file,
                        account_id=account.account_id,
                        as_of=as_of_str,
                    )
                )
        out.append(
            observation(
                field="account_value",
                value=account.value,
                source_role="db",
                source_file=source_file,
                account_id=account.account_id,
                as_of=as_of_str,
                kind="account_balance",
            )
        )
    return out


def _observations_from_extract(
    extracted: MeetingExtract,
    *,
    source_role: SourceRole,
    source_file: str,
    default_as_of: str | None = None,
    known_account_ids: set[str] | None = None,
) -> list[Observation]:
    out: list[Observation] = []
    for item in extracted.observations:
        value = item.value
        if item.field == "selling" and isinstance(value, str):
            coerced = _coerce_selling(value)
            if coerced is None:
                continue
            value = coerced
        as_of = item.as_of or default_as_of
        account_id = item.account_id
        if account_id is not None and known_account_ids is not None and account_id not in known_account_ids:
            account_id = None
        kind = item.kind if item.kind in MONEY_KINDS else None
        out.append(
            observation(
                field=item.field,
                value=value,
                source_role=source_role,
                source_file=source_file,
                as_of=as_of,
                account_id=account_id,
                quote=item.quote,
                approximate=item.approximate,
                kind=kind,
            )
        )
    return out


def _format_known_accounts(accounts: list[KnownAccount]) -> str:
    if not accounts:
        return "(none — leave account_id null)"
    return "\n".join(account.line() for account in accounts)


def extract_meeting_observations(
    text: str,
    source_file: str,
    *,
    openai_client: OpenAI,
    model: str,
    known_accounts: list[KnownAccount] | None = None,
) -> list[Observation]:
    """One structured LLM extraction over meeting notes."""
    accounts = known_accounts or []
    known_ids = {account.account_id for account in accounts}
    prompt = _MEETING_EXTRACT_PROMPT.format(known_accounts=_format_known_accounts(accounts))
    payload = JsonChat(openai_client, model).complete(f"{prompt}\n\n---\n\n{text}", temperature=0)
    if not payload:
        return []
    extracted = MeetingExtract.from_payload(payload)
    return _observations_from_extract(
        extracted,
        source_role="meeting",
        source_file=source_file,
        default_as_of=extracted.meeting_date,
        known_account_ids=known_ids or None,
    )


def _llm_structured_fallback(
    text: str,
    *,
    source_file: str,
    source_role: SourceRole,
    openai_client: OpenAI | None,
    model: str | None,
) -> list[Observation]:
    if openai_client is None or not model:
        return []
    payload = JsonChat(openai_client, model).complete(
        f"{_STRUCTURED_FALLBACK_PROMPT}\n\n---\n\n{text}"
    )
    if not payload:
        return []
    return _observations_from_extract(
        MeetingExtract.from_payload(payload),
        source_role=source_role,
        source_file=source_file,
    )


def _known_accounts_from_db_obs(observations: list[Observation]) -> list[KnownAccount]:
    by_id: dict[str, KnownAccount] = {}
    for obs in observations:
        if obs.source_role != "db" or not obs.account_id:
            continue
        account = by_id.setdefault(obs.account_id, KnownAccount(account_id=obs.account_id))
        if obs.field == "account_type":
            account.type = obs.value
        elif obs.field == "account_platform":
            account.platform = obs.value
        elif obs.field == "account_owner":
            account.owner = obs.value
    return list(by_id.values())


def extract_observations(
    typed_sources: dict[str, TypedSource | dict[str, Any]],
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> list[Observation]:
    """Extract all observations from typed retrieve sources (request/meeting/db)."""
    sources = {
        role: TypedSource.from_mapping(role, src) for role, src in typed_sources.items()
    }
    observations: list[Observation] = []

    if "request" in sources:
        src = sources["request"]
        observations.extend(
            extract_request_observations(
                src.text,
                src.name,
                openai_client=openai_client,
                model=model,
            )
        )

    if "db" in sources:
        src = sources["db"]
        if src.path.suffix.lower() == ".json":
            observations.extend(
                extract_db_observations(
                    src.path,
                    src.name,
                    openai_client=openai_client,
                    model=model,
                )
            )
        else:
            observations.extend(
                extract_db_observations(
                    src.text,
                    src.name,
                    openai_client=openai_client,
                    model=model,
                )
            )

    if "meeting" in sources and openai_client is not None and model:
        src = sources["meeting"]
        observations.extend(
            extract_meeting_observations(
                src.text,
                src.name,
                openai_client=openai_client,
                model=model,
                known_accounts=_known_accounts_from_db_obs(observations),
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
