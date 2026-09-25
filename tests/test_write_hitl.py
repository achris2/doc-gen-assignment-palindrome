"""Tests for deterministic HITL footer and fact-bound renderers."""

from agent_pipeline.hitl import append_hitl_footer, render_human_review, render_sources
from agent_pipeline.reconcile import build_case_document, reconcile_observations
from agent_pipeline.render import (
    account_in_scope,
    render_cgt_statement,
    render_decision_sentences,
    render_fees,
    render_material_context,
    render_risk_profile_line,
    render_holdings_table,
    render_scope,
    resolve_scoped_accounts,
)
from agent_pipeline.schema import (
    Account,
    Observation,
    ReconciledFacts,
    SectionSpec,
    SourcedValue,
    project_money,
)


def _obs(field, value, role, *, source_file, account_id=None, as_of=None):
    return Observation(
        field=field,
        value=value,
        source_role=role,
        source_file=source_file,
        account_id=account_id,
        as_of=as_of,
    )


def test_money_projection_keeps_balances_and_movements_out_of_context() -> None:
    assert (project_money("account_balance").role, project_money("account_balance").availability) == (
        "balance",
        "unknown",
    )
    assert (project_money("transfer_amount").role, project_money("transfer_amount").availability) == (
        "movement",
        "unknown",
    )
    assert (project_money("received_proceeds").role, project_money("received_proceeds").availability) == (
        "other",
        "available",
    )
    assert (project_money("loan_repayment").role, project_money("loan_repayment").availability) == (
        "other",
        "unavailable",
    )
    assert (project_money("contingent_proceeds").role, project_money("contingent_proceeds").availability) == (
        "other",
        "contingent",
    )
    balance = _obs("account_value", 52000, "db", source_file="db.json", account_id="H-ISA-01")
    balance.kind = "account_balance"
    transfer = _obs("investment_amount", 20000, "meeting", source_file="meeting.docx", account_id="H-CASH-01")
    transfer.kind = "transfer_amount"
    case = build_case_document(reconcile_observations([balance, transfer]), {})
    assert render_material_context(case.facts) == ""


def test_risk_profile_line_is_the_stored_fact_and_skips_a_conflict() -> None:
    known = _obs("risk_profile", "4 (balanced to moderate)", "request", source_file="report_request.docx")
    moderate = _obs("risk_profile", "4 (moderate)", "request", source_file="report_request.docx")
    conflicted = reconcile_observations(
        [
            _obs("risk_profile", "4 (moderate)", "request", source_file="report_request.docx"),
            _obs("risk_profile", "5 (balanced)", "meeting", source_file="meeting.docx"),
        ]
    )
    assert render_risk_profile_line(
        build_case_document(reconcile_observations([known]), {}).facts
    ) == "Your risk profile is 4, described as balanced to moderate."
    assert render_risk_profile_line(
        build_case_document(reconcile_observations([moderate]), {}).facts
    ) == "Your risk profile is 4, described as moderate."
    assert "£" not in render_risk_profile_line(
        build_case_document(reconcile_observations([known]), {}).facts
    )
    assert render_risk_profile_line(build_case_document(conflicted, {}).facts) == ""


def test_context_paragraph_links_a_repayment_to_its_pool() -> None:
    received = _obs("received_proceeds", 850000, "meeting", source_file="meeting_notes.docx")
    received.kind = "received_proceeds"
    received.quote = "A completion payment of £850,000 was received on completion."
    repayment = _obs("loan_repayment", 200000, "meeting", source_file="meeting_notes.docx")
    repayment.kind = "loan_repayment"
    repayment.quote = (
        "Important point on the completion money: £200,000 of the £850,000 "
        "is already committed to repaying a bridging loan James took out last year."
    )
    contingent = _obs("contingent_proceeds", 400000, "meeting", source_file="meeting_notes.docx")
    contingent.kind = "contingent_proceeds"
    case = build_case_document(reconcile_observations([received, repayment, contingent]), {})
    text = render_material_context(case.facts)
    assert "£850,000 completion money received" in text
    assert "£200,000 is not available to invest because it is committed to repaying a bridging loan" in text
    assert "up to £400,000 is contingent" in text
    assert "James" not in text
    assert "## Funding" not in text


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
    assert render_cgt_statement(facts).startswith("The disposal may create")
    named = render_cgt_statement(
        facts,
        type("Case", (), {
            "decisions": [
                type("Decision", (), {
                    "type": "dispose",
                    "target_account_id": "H4-GIA-HJ",
                    "amount": None,
                })()
            ],
            "accounts": [
                type("Account", (), {"account_id": "H4-GIA-HJ", "type": "General Investment Account"})()
            ],
        })(),
    )
    assert "H4-GIA-HJ (General Investment Account)" in named
    assert named.startswith("The disposal of")
    assert "£" not in named.replace("[REVIEW: CGT figure]", "")
    partial = render_cgt_statement(
        facts,
        type("Case", (), {
            "decisions": [
                type("Decision", (), {
                    "type": "dispose",
                    "target_account_id": "H4-GIA-HJ",
                    "amount": None,
                    "subject": "disinvest a portion of the Holloway joint GIA",
                })()
            ],
            "accounts": [
                type("Account", (), {"account_id": "H4-GIA-HJ", "type": "General Investment Account"})()
            ],
        })(),
    )
    assert partial.startswith("A partial disposal of H4-GIA-HJ")


def test_decision_sentences_cover_dispose_retain_and_confirm() -> None:
    from agent_pipeline.schema import MeetingDecision

    case = build_case_document(
        reconcile_observations(
            [
                _obs(
                    "recommendation_summary",
                    "use both ISA allowances and place the remainder in a new joint account",
                    "meeting",
                    source_file="meeting_notes.docx",
                )
            ]
        ),
        {},
        meeting_decisions=[
            MeetingDecision(
                type="dispose",
                status="agreed",
                quote="we agreed to disinvest a portion of the Holloway joint GIA",
                source_file="meeting_notes.docx",
                subject="disinvest a portion of the Holloway joint GIA",
                target_account_id="H4-GIA-HJ",
            ),
            MeetingDecision(
                type="retain",
                status="agreed",
                quote="we agreed to leave the offshore bond as it is for now",
                source_file="meeting_notes.docx",
                subject="leave the offshore bond as it is for now",
                target_account_id="M4-BOND-J",
            ),
            MeetingDecision(
                type="confirm",
                status="outstanding",
                quote="we will confirm the cash balance before anything is finalised",
                source_file="meeting_notes.docx",
                subject="the outstanding cash balance",
                target_account_id="H-CASH-JE",
            ),
        ],
    )
    text = render_decision_sentences(case.decisions)
    assert "We agreed to disinvest a portion of the Holloway joint GIA." in text
    assert "We agreed to leave the offshore bond as it is for now." in text
    assert "Still to confirm: the outstanding cash balance." in text
    assert "£" not in text
    assert not any(decision.type in {"dispose", "retain", "confirm"} and decision.id.startswith("a-") for decision in case.decisions)


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


def _account(account_id: str, typ: str, *, status: str | None = None, value: object = 1000) -> Account:
    account = Account(account_id=account_id, type=typ, value=value, status=status)
    account._assigned.update({"type", "value"})
    if status is not None:
        account._assigned.add("status")
    return account


def _covered(text: str, accounts: list[Account]) -> ReconciledFacts:
    return ReconciledFacts(
        selling=False,
        conflicts=[],
        review_items=[],
        facts={"accounts_covered": SourcedValue(value=text, source="request", source_file="req")},
        accounts=accounts,
        observation_count=0,
    )


def test_account_in_scope_matches_type_tokens() -> None:
    isa = _account("H-ISA-01", "Stocks & Shares ISA")
    gia = _account("H-GIA-J", "General Investment Account")
    cash = _account("H-CASH-01", "Cash")
    unknown = _account("H-99", "")

    assert account_in_scope(isa, "")
    assert account_in_scope(isa, "the Holloway portfolio")
    assert account_in_scope(unknown, "ISA only")
    assert account_in_scope(isa, "Stocks and shares ISA")
    assert account_in_scope(gia, "the general investment account")
    assert not account_in_scope(gia, "ISA only")
    assert not account_in_scope(cash, "SIPP")
    assert account_in_scope(cash, "cash and ISA")


def test_resolve_scoped_accounts_drops_closed_and_out_of_scope() -> None:
    facts = _covered(
        "ISA only",
        [
            _account("H-ISA-01", "ISA", status="open"),
            _account("H-ISA-OLD", "ISA", status="Closed"),
            _account("H-GIA-J", "GIA"),
        ],
    )
    ids = [account.account_id for account in resolve_scoped_accounts(facts)]
    assert ids == ["H-ISA-01"]


def test_resolve_scoped_accounts_adds_one_synthetic_new_joint() -> None:
    existing = _account("H-ISA-01", "ISA")
    facts = _covered("new joint account plus the ISA", [existing])
    scoped = resolve_scoped_accounts(facts)
    assert [account.account_id for account in scoped] == ["H-ISA-01", "New joint account"]
    assert scoped[-1].synthetic is True
    assert scoped[-1].type == "To be opened"

    already = _account("NEW-1", "GIA")
    already.mark_synthetic()
    again = resolve_scoped_accounts(_covered("a new joint account and the ISA", [already, existing]))
    assert [account.account_id for account in again] == ["NEW-1", "H-ISA-01"]


def test_holdings_table_empty_and_synthetic_row() -> None:
    empty = render_holdings_table(_covered("ISA", []))
    assert "| [REVIEW: accounts] | — | — | — |" in empty

    table = render_holdings_table(
        _covered("new joint account and the ISA", [_account("H-ISA-01", "ISA", value=52000)])
    )
    assert "New joint account" in table
    assert "To be opened" in table
    assert "n/a" in table
    assert "£52,000" in table


def test_human_review_empty_conflicts_message() -> None:
    facts = reconcile_observations([_obs("selling", False, "request", source_file="r")])
    text = render_human_review(facts)
    assert "No open conflicts." in text
    assert "[REVIEW: platform fee]" in text
    sources = render_sources(facts)
    assert "| Fact area | Source |" in sources
