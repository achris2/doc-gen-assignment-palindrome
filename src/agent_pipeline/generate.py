"""Write stage: fill the template from reconciled facts.

The application entry point is agent_pipeline.main.
"""

import json
from typing import Any

from openai import OpenAI

from document_formatter.formatting import format_document

from agent_pipeline.reconcile import validate_recommendation_items
from agent_pipeline.render import (
    facts_context_block,
    render_cgt_statement,
    render_fees,
    render_holdings_table,
    render_scope,
)

# Placeholders filled from reconciled facts (no LLM).
# Prefer placeholder "source": "render" | "llm" in config; fall back to this set.
_DETERMINISTIC_SLOTS = {
    "scope": render_scope,
    "holdings_table": render_holdings_table,
    "fees": render_fees,
    "cgt_statement": render_cgt_statement,
}


class ReportGenerator:
    """Builds a report from reconciled facts + narrative LLM slots."""

    def __init__(self, openai_client: OpenAI, model: str) -> None:
        self._openai = openai_client
        self._model = model

    def generate(
        self,
        config: dict,
        *,
        facts: dict[str, Any],
        case: dict[str, Any],
    ) -> str:
        instructions = config.get("global_instructions", "")
        sections: list[dict[str, str]] = []
        for section in config["sections"]:
            if not self._section_applies(section, facts):
                continue
            sections.append(
                {
                    "title": section.get("title", ""),
                    "content": self._build_section(
                        section, facts, case, instructions
                    ),
                }
            )
        return format_document(config, sections)

    def _section_applies(self, section: dict, facts: dict[str, Any]) -> bool:
        rule = section.get("use_if", "always")
        if rule == "always":
            return True
        if isinstance(rule, dict):
            fact_name = rule.get("fact", "selling")
            equals = rule.get("equals", True)
            actual = facts.get(fact_name)
            return bool(actual) == bool(equals)
        # Legacy plain-language: Tax / selling gate
        if "sold" in str(rule).lower() or "selling" in str(rule).lower():
            return bool(facts.get("selling"))
        raise ValueError(f"Unknown use_if rule: {rule!r}")

    def _placeholder_is_render(self, name: str, spec: dict) -> bool:
        source = spec.get("source")
        if source == "render":
            return True
        if source == "llm":
            return False
        return name in _DETERMINISTIC_SLOTS

    def _build_section(
        self,
        section: dict,
        facts: dict[str, Any],
        case: dict[str, Any],
        instructions: str,
    ) -> str:
        content = section["template"]
        for name, spec in section.get("placeholders", {}).items():
            if self._placeholder_is_render(name, spec):
                renderer = _DETERMINISTIC_SLOTS.get(name)
                if renderer is None:
                    raise ValueError(f"No render function for placeholder {name!r}")
                value = renderer(facts)
            elif spec.get("input") == "actions":
                value = self._recommendation_items(spec, case, instructions)
            else:
                value = self._narrative_slot(name, spec, case, instructions)
            content = content.replace(f"<<{name}>>", value)
        return content

    def _recommendation_items(self, spec: dict, case: dict[str, Any], instructions: str) -> str:
        payload = self._ask_json(
            f"{instructions}\n\n{spec['prompt']}\n"
            "Return JSON {\"items\": [{\"action_id\": \"...\", \"text\": \"...\"}]}. "
            "One item for every action id above. Do not choose which amount belongs to which action.",
            slot_context(case, spec),
        )
        text, stored = validate_recommendation_items(payload or {}, case.get("actions") or [])
        case.setdefault("sections", {})["recommendation"] = {"items": stored}
        return text

    def _narrative_slot(
        self, name: str, spec: dict, case: dict[str, Any], instructions: str
    ) -> str:
        payload = self._ask_json(
            f"{instructions}\n\n{spec['prompt']}\n"
            'Return JSON {"text": "...", "fact_ids": ["f-..."]}.',
            slot_context(case, spec),
        )
        if not payload or "text" not in payload:
            case.setdefault("sections", {})[name] = {"fact_ids": []}
            return f"[REVIEW: {name} uncited]"
        fact_ids = [str(item) for item in payload.get("fact_ids") or []]
        case.setdefault("sections", {})[name] = {"fact_ids": fact_ids}
        return str(payload.get("text") or "").strip()

    def _ask_json(self, instruction: str, context: str) -> dict[str, Any] | None:
        try:
            response = self._openai.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "user", "content": f"{context}\n\n---\n\n{instruction}"}
                ],
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


def slot_context(case: dict[str, Any], spec: dict[str, Any]) -> str:
    """Facts or actions for one placeholder. No raw source documents."""
    if spec.get("input") == "actions":
        lines = ["RECOMMENDATION ACTIONS (already joined — do not reassign amounts):"]
        for action in case.get("actions") or []:
            lines.append(
                f"- id={action.get('id')} amount={action.get('amount')!r} "
                f"who={action.get('who')!r} product={action.get('product')!r} "
                f"source_of_funds={action.get('source_of_funds')!r} "
                f"summary={action.get('summary')!r} "
                f"supports={action.get('supports')!r}"
            )
        return "\n".join(lines)
    wanted = spec.get("facts")
    lines = ["CASE FACTS (use only these; cite their ids):"]
    for fact in case.get("facts") or []:
        if wanted and fact.get("field") not in wanted:
            continue
        lines.append(
            f"- id={fact.get('id')} field={fact.get('field')} value={fact.get('value')!r} "
            f"source_file={fact.get('source_file')!r} excerpt={fact.get('excerpt')!r}"
        )
    return "\n".join(lines)


def build_narrative_context(
    facts: dict[str, Any],
    typed_sources: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Legacy full block. Narrative slots use slot_context instead."""
    del typed_sources
    return facts_context_block(facts)
