"""Tests for deterministic HITL footer and fact-bound renderers."""

from agent_pipeline.hitl import append_hitl_footer, render_human_review, render_sources
from agent_pipeline.reconcile import reconcile_observations
from agent_pipeline.render import (
    render_cgt_statement,
    render_fees,
    render_holdings_table,
    render_scope,
)
from agent_pipeline.schema import Observation, ReconciledFacts, SectionSpec


def _obs(field, value, role, *, source_file, account_id=None, as_of=None):
    return Observation(
        field=field,
        value=value,
        source_role=role,
        source_file=source_file,
        account_id=account_id,
        as_of=as_of,
    )


def _sample_facts():
    return reconcile_observations(
        [
            _obs("selling", True, "request", source_file="report_request.docx"),
            _obs(
                "accounts_covered",
                "Holloway ISAs, joint GIA, and cash accounts",
                "request",
                source_file="report_request.docx",
            ),
            _obs("initial_charge", "0.5%", "request", source_file="report_request.docx"),
            _obs("account_value", 40000, "db", source_file="db.json", account_id="H-GIA-J", as_of="2026-03-15"),
            _obs("account_type", "GIA", "db", source_file="db.json", account_id="H-GIA-J"),
            _obs("account_owner", "Joint", "db", source_file="db.json", account_id="H-GIA-J"),
            _obs(
                "account_value",
                45000,
                "meeting",
                source_file="meeting.docx",
                account_id="H-GIA-J",
                as_of="2026-05-14",
            ),
            _obs("account_value", None, "db", source_file="db.json", account_id="H-CASH-JE"),
            _obs("account_type", "Cash Account", "db", source_file="db.json", account_id="H-CASH-JE"),
            _obs("account_owner", "Jean", "db", source_file="db.json", account_id="H-CASH-JE"),
        ]
    )


def test_render_scope_and_fees_and_cgt() -> None:
    facts = _sample_facts()
    assert "Holloway ISAs" in render_scope(facts)
    fees = render_fees(facts)
    assert "[REVIEW: platform fee]" in fees
    assert "[REVIEW: advice fee]" in fees
    assert "0.5%" in fees
    assert "Initial charge" in fees
    assert "[REVIEW: CGT figure]" in render_cgt_statement(facts)


def test_holdings_table_shows_conflict_and_null_review() -> None:
    facts = _sample_facts()
    table = render_holdings_table(facts)
    assert "H-GIA-J" in table
    assert "£45,000" in table or "45000" in table
    assert "REVIEW" in table
    assert "H-CASH-JE" in table
    assert "[REVIEW: H-CASH-JE value]" in table


def test_hitl_footer_always_present() -> None:
    facts = _sample_facts()
    report = (
        "# Investment Advice Report\n\n"
        "## Conclusion\n\nDone.\n"
        "[REVIEW: confirm ongoing charges after report issuance]\n"
    )
    out = append_hitl_footer(
        report,
        facts,
        {"report_request.docx": "request", "meeting_notes.docx": "meeting", "db.json": "db"},
    )
    assert "## Human review" in out
    assert "### Conflicts" in out
    assert "account_value:H-GIA-J" in out
    assert "## Sources" in out
    assert "report_request.docx" in out or "db.json" in out
    assert "[REVIEW: platform fee]" in out
    assert "[REVIEW: confirm ongoing charges after report issuance]" in out


def _facts(selling: bool) -> ReconciledFacts:
    return ReconciledFacts(
        selling=selling,
        conflicts=[],
        review_items=[],
        facts={},
        accounts=[],
        observation_count=0,
    )


def test_tax_section_gated_by_selling() -> None:
    tax = SectionSpec.from_dict(
        {
            "use_if": "Include only when selling is true in reconciled facts.",
            "title": "Tax Implications",
            "template": "",
        }
    )
    always = SectionSpec.from_dict({"use_if": "always", "template": ""})
    assert tax.applies(_facts(True)) is True
    assert tax.applies(_facts(False)) is False
    assert always.applies(_facts(False)) is True


def test_human_review_empty_conflicts_message() -> None:
    facts = reconcile_observations([_obs("selling", False, "request", source_file="r")])
    text = render_human_review(facts)
    assert "No open conflicts." in text
    assert "[REVIEW: platform fee]" in text
    sources = render_sources(facts)
    assert "| Fact area | Source |" in sources
