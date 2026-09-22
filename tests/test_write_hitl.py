"""Tests for deterministic HITL footer and fact-bound renderers."""

from agent_pipeline.hitl import append_hitl_footer, render_human_review, render_sources
from agent_pipeline.reconcile import reconcile_observations
from agent_pipeline.render import (
    render_cgt_statement,
    render_fees,
    render_holdings_table,
    render_scope,
)
from agent_pipeline.generate import ReportGenerator


def _sample_facts():
    return reconcile_observations(
        [
            {
                "field": "selling",
                "value": True,
                "source_role": "request",
                "source_file": "report_request.docx",
            },
            {
                "field": "accounts_covered",
                "value": "Holloway ISAs and joint GIA",
                "source_role": "request",
                "source_file": "report_request.docx",
            },
            {
                "field": "initial_charge",
                "value": "0.5%",
                "source_role": "request",
                "source_file": "report_request.docx",
            },
            {
                "field": "account_value",
                "value": 40000,
                "source_role": "db",
                "source_file": "db.json",
                "account_id": "H-GIA-J",
                "as_of": "2026-03-15",
            },
            {
                "field": "account_type",
                "value": "GIA",
                "source_role": "db",
                "source_file": "db.json",
                "account_id": "H-GIA-J",
            },
            {
                "field": "account_owner",
                "value": "Joint",
                "source_role": "db",
                "source_file": "db.json",
                "account_id": "H-GIA-J",
            },
            {
                "field": "account_value",
                "value": 45000,
                "source_role": "meeting",
                "source_file": "meeting.docx",
                "account_id": "H-GIA-J",
                "as_of": "2026-05-14",
            },
            {
                "field": "account_value",
                "value": None,
                "source_role": "db",
                "source_file": "db.json",
                "account_id": "H-CASH-JE",
            },
            {
                "field": "account_type",
                "value": "Cash Account",
                "source_role": "db",
                "source_file": "db.json",
                "account_id": "H-CASH-JE",
            },
            {
                "field": "account_owner",
                "value": "Jean",
                "source_role": "db",
                "source_file": "db.json",
                "account_id": "H-CASH-JE",
            },
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
    assert "conflict" in table.lower() or "REVIEW" in table
    assert "H-CASH-JE" in table
    assert "[REVIEW: H-CASH-JE value]" in table


def test_hitl_footer_always_present() -> None:
    facts = _sample_facts()
    report = "# Investment Advice Report\n\n## Conclusion\n\nDone.\n"
    out = append_hitl_footer(
        report,
        facts,
        {"report_request.docx": "request", "meeting_notes.docx": "meeting", "db.json": "db"},
    )
    assert "## Human review" in out
    assert "### Conflicts" in out
    assert "account_value:H-GIA-J" in out
    assert "## Sources" in out
    assert "retrieve:request" in out
    assert "[REVIEW: platform fee]" in out


def test_tax_section_gated_by_selling() -> None:
    class Dummy:
        pass

    gen = ReportGenerator.__new__(ReportGenerator)
    selling_yes = {"selling": True}
    selling_no = {"selling": False}
    tax = {
        "use_if": "Include only when selling is true in reconciled facts.",
        "title": "Tax Implications",
    }
    assert gen._section_applies(tax, selling_yes) is True
    assert gen._section_applies(tax, selling_no) is False
    assert gen._section_applies({"use_if": "always"}, selling_no) is True


def test_human_review_empty_conflicts_message() -> None:
    facts = reconcile_observations(
        [
            {
                "field": "selling",
                "value": False,
                "source_role": "request",
                "source_file": "r",
            }
        ]
    )
    text = render_human_review(facts)
    assert "No open conflicts." in text
    assert "[REVIEW: platform fee]" in text
    sources = render_sources(facts)
    assert "| Fact area | Source |" in sources
