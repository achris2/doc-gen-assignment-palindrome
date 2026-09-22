"""Retrieve: classify client file-dump roles — minimal deterministic checks + LLM fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI

from document_formatter.loading import read_file

Role = Literal["request", "meeting", "db", "noise", "internal"]

CORE_ROLES: frozenset[str] = frozenset({"request", "meeting", "db"})
WRITER_ROLES: frozenset[str] = CORE_ROLES

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PREVIEW_CHARS = 1200

_LLM_CLASSIFY_PROMPT = """\
Classify each file preview for an investment-advice report pipeline.
Pick exactly one role per file.

Roles:
- request: Adviser instructions for THIS report — scope, accounts covered, recommended
  product, transfer/sell flags, amounts. Often a short requirement summary or key/value table.
- meeting: File notes or narrative of a SPECIFIC client meeting — discussion, decisions,
  circumstances, objectives, live account figures mentioned in conversation.
- db: Structured account/holdings data (CRM or custody snapshot) — account ids, valuations,
  holders, platforms. May be JSON or tabular extracts.
- internal: Technical report specs, colleague pipeline notes, or instructions about how to
  build the report — not client-facing advice content.
- noise: General market commentary, model/portfolio packs, marketing, generic disclaimers,
  images/unreadable content, or anything not specific to this client's case.

Disambiguation:
- If text is general marketing/market update (even if it says "client" or "meeting") → noise.
- If unsure between roles → noise.
- Prefer request for imperative/instruction tables; meeting for narrative discussion notes.

Return ONLY valid JSON:
{"classifications": [{"name": "<filename>", "role": "<role>"}]}
Unknown or unclear → role "noise".
"""


def _read_text_prefix(path: Path, limit: int) -> str:
    try:
        text = read_file(path)
    except Exception:
        return ""
    return text[:limit]


def _preview_text(path: Path) -> str:
    return _read_text_prefix(path, PREVIEW_CHARS)


def classify_by_pattern(path: Path, text: str | None = None) -> Role | None:
    """Deterministic zero-ambiguity checks only; None → route to LLM.

    Rules:
    - image extensions → noise
    - JSON with top-level "holders" → db

    ``text`` is accepted for call-site compatibility but ignored.
    """
    _ = text
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "noise"

    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict) and "holders" in data:
            return "db"

    return None


def classify_unmatched_with_llm(
    files: list[tuple[Path, str]],
    openai_client: OpenAI,
    model: str,
) -> dict[str, Role]:
    """One batched LLM call for files patterns could not classify. Failures → noise."""
    if not files:
        return {}

    default: dict[str, Role] = {path.name: "noise" for path, _ in files}

    lines = []
    for path, preview in files:
        body = preview.strip() or "[unreadable or empty]"
        lines.append(f"### {path.name}\n{body}")

    try:
        response = openai_client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": _LLM_CLASSIFY_PROMPT + "\n\nFiles:\n" + "\n\n".join(lines),
                }
            ],
        )
        raw = response.choices[0].message.content or "{}"
        payload = json.loads(raw)
    except Exception:
        return default

    allowed: set[str] = {"request", "meeting", "db", "noise", "internal"}
    out = dict(default)
    for item in payload.get("classifications") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        role = item.get("role")
        if name in out and role in allowed:
            out[name] = role  # type: ignore[assignment]
    return out


def classify_client_files(
    client_dir: Path,
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> dict[str, Role]:
    """Classify every file in the client dump. Unmatched without LLM → noise."""
    roles: dict[str, Role] = {}
    unmatched: list[tuple[Path, str]] = []

    for path in sorted(client_dir.iterdir()):
        if not path.is_file():
            continue
        role = classify_by_pattern(path)
        if role is None:
            unmatched.append((path, _preview_text(path)))
        else:
            roles[path.name] = role

    if unmatched and openai_client is not None and model:
        llm_roles = classify_unmatched_with_llm(unmatched, openai_client, model)
        roles.update(llm_roles)
    else:
        for path, _ in unmatched:
            roles[path.name] = "noise"

    return roles


def select_context_files(
    client_dir: Path,
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
    roles: frozenset[str] = WRITER_ROLES,
) -> list[str]:
    """Filenames whose roles are allowed in writer/extract context (default: core only)."""
    classified = classify_client_files(
        client_dir, openai_client=openai_client, model=model
    )
    return sorted(name for name, role in classified.items() if role in roles)


def find_path_by_role(client_dir: Path, role: Role, classifications: dict[str, Role]) -> Path | None:
    for name, assigned in classifications.items():
        if assigned == role:
            return client_dir / name
    return None


def find_db_path(client_dir: Path, classifications: dict[str, Role] | None = None) -> Path | None:
    roles = classifications or classify_client_files(client_dir)
    return find_path_by_role(client_dir, "db", roles)


def find_request_path(
    client_dir: Path, classifications: dict[str, Role] | None = None
) -> Path | None:
    roles = classifications or classify_client_files(client_dir)
    return find_path_by_role(client_dir, "request", roles)


def roles_summary(classifications: dict[str, Role]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {
        "request": [],
        "meeting": [],
        "db": [],
        "noise": [],
        "internal": [],
    }
    for name, role in sorted(classifications.items()):
        summary.setdefault(role, []).append(name)
    return summary


def load_typed_sources(
    client_dir: Path,
    *,
    openai_client: OpenAI | None = None,
    model: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return typed sources: role → {path, name, text} for core roles only."""
    classifications = classify_client_files(
        client_dir, openai_client=openai_client, model=model
    )
    typed: dict[str, dict[str, Any]] = {}
    for name, role in classifications.items():
        if role not in CORE_ROLES:
            continue
        path = client_dir / name
        typed[role] = {
            "path": path,
            "name": name,
            "text": read_file(path),
            "role": role,
        }
    return typed
