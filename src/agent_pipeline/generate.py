"""Write stage: fill the template from reconciled facts.

The application entry point is agent_pipeline.main.
"""

from typing import Callable

from openai import OpenAI

from document_formatter.formatting import format_document

from agent_pipeline.llm import JsonChat
from agent_pipeline.reconcile import validate_recommendation_items
from agent_pipeline.render import (
    facts_context_block,
    render_cgt_statement,
    render_fees,
    render_material_context,
    render_risk_profile_line,
    render_holdings_table,
    render_scope,
)
from agent_pipeline.schema import (
    CaseDocument,
    CaseFact,
    NarrativeDraft,
    PlaceholderSpec,
    RecommendationAction,
    RecommendationDraft,
    ReconciledFacts,
    SectionSpec,
    TemplateConfig,
)

RenderFn = Callable[[ReconciledFacts], str]

RENDERERS: dict[str, RenderFn] = {
    "scope": render_scope,
    "holdings_table": render_holdings_table,
    "fees": render_fees,
    "cgt_statement": render_cgt_statement,
}
_RENDER_NAMES = frozenset(RENDERERS)


class ReportGenerator:
    """Builds a report from reconciled facts and narrative slots."""

    def __init__(self, openai_client: OpenAI, model: str) -> None:
        self._chat = JsonChat(openai_client, model)

    def generate(
        self,
        config: TemplateConfig,
        *,
        facts: ReconciledFacts,
        case: CaseDocument,
    ) -> str:
        sections = []
        for section in config.sections:
            if not section.applies(facts):
                continue
            content = self._build_section(section, facts, case, config.global_instructions)
            if not content.strip():
                continue
            sections.append({"title": section.title, "content": content})
        return format_document(config.document_dict(), sections)

    def _build_section(
        self,
        section: SectionSpec,
        facts: ReconciledFacts,
        case: CaseDocument,
        instructions: str,
    ) -> str:
        content = section.template
        for name, spec in section.placeholders.items():
            content = content.replace(f"<<{name}>>", self._fill(name, spec, facts, case, instructions))
        return content

    def _fill(
        self,
        name: str,
        spec: PlaceholderSpec,
        facts: ReconciledFacts,
        case: CaseDocument,
        instructions: str,
    ) -> str:
        if spec.is_render(name, _RENDER_NAMES):
            renderer = RENDERERS.get(name)
            if renderer is None:
                raise ValueError(f"No render function for placeholder {name!r}")
            if name == "cgt_statement":
                return render_cgt_statement(facts, case)
            return renderer(facts)
        if spec.is_actions():
            return self._recommendation_items(spec, case, instructions)
        return self._narrative_slot(name, spec, case, instructions)

    def _recommendation_items(
        self, spec: PlaceholderSpec, case: CaseDocument, instructions: str
    ) -> str:
        if not case.actions:
            case.record_recommendation([])
            return _with_context("The amounts to be invested have not been fixed.", case)
        if all(action.amount_status == "not_agreed" for action in case.actions):
            return _with_context(_unfixed_recommendation(case), case)
        payload = self._chat.complete(
            f"{_action_context(case.actions)}\n\n---\n\n{instructions}\n\n{spec.prompt}\n"
            'Return JSON {"items": [{"action_id": "...", "text": "..."}]}. '
            "One item for every action id above. Do not choose which amount belongs to which action."
        )
        text, stored = validate_recommendation_items(
            RecommendationDraft.from_payload(payload), case.actions
        )
        case.record_recommendation(stored)
        return _with_context(text, case)

    def _narrative_slot(
        self, name: str, spec: PlaceholderSpec, case: CaseDocument, instructions: str
    ) -> str:
        payload = self._chat.complete(
            f"{_fact_context(case.facts_for(list(spec.facts) or None))}\n\n---\n\n"
            f"{instructions}\n\n{spec.prompt}\n"
            'Return JSON {"text": "...", "fact_ids": ["f-..."]}.'
        )
        draft = NarrativeDraft.from_payload(payload)
        if draft is None:
            case.record_narrative(name, [])
            return f"[REVIEW: {name} uncited]"
        case.record_narrative(name, draft.fact_ids)
        if name == "summary":
            return _with_risk_profile(draft.text, case)
        return draft.text


def _with_risk_profile(text: str, case: CaseDocument) -> str:
    line = render_risk_profile_line(case.facts)
    if not line or line in text:
        return text
    body = text.rstrip()
    if not body:
        return line
    return f"{body}\n\n{line}"


def _with_context(text: str, case: CaseDocument) -> str:
    extra = render_material_context(case.facts)
    if not extra:
        return text
    return f"{text.rstrip()} {extra}"


def _unfixed_recommendation(case: CaseDocument) -> str:
    """Agreed destinations with no figure. The wording is the stored summary."""
    action = case.actions[0]
    summary = str(action.summary or "").strip()
    if summary and not summary.endswith("."):
        summary += "."
    text = f"{summary} The amounts have not yet been finalised.".strip()
    from agent_pipeline.schema import RecommendationItem

    case.record_recommendation([RecommendationItem(action_id=action.id, text=text)])
    return text


def _action_context(actions: list[RecommendationAction]) -> str:
    lines = ["RECOMMENDATION ACTIONS (already joined — do not reassign amounts):"]
    lines.extend(action.line() for action in actions)
    return "\n".join(lines)


def _fact_context(facts: list[CaseFact]) -> str:
    lines = ["CASE FACTS (use only these; cite their ids):"]
    for fact in facts:
        lines.append(
            f"- id={fact.id} field={fact.field} value={fact.value!r} "
            f"source_file={fact.source_file!r} excerpt={fact.excerpt!r}"
        )
    return "\n".join(lines)


def slot_context(case: CaseDocument, spec: PlaceholderSpec) -> str:
    """Facts or actions for one placeholder. No raw source documents."""
    if spec.is_actions():
        return _action_context(case.actions)
    return _fact_context(case.facts_for(list(spec.facts) or None))


def build_narrative_context(facts: ReconciledFacts) -> str:
    """Legacy full block. Narrative slots use slot_context instead."""
    return facts_context_block(facts)
