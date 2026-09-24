"""Smoke tests for deterministic report eval."""

from pathlib import Path

from eval.checks import (
    FCA_LINE,
    RISK_WARNING,
    CheckResult,
    ClientBundle,
    check_fca_line,
    check_no_pounds_in_background_summary,
    check_risk_warning,
    check_tax_iff_selling,
    delta_against_history,
    discover_clients,
    format_eval,
    format_history,
    format_scorecard,
    make_record,
    read_run_note,
    run_checks,
)


def _bundle(**kwargs) -> ClientBundle:
    defaults = dict(
        name="client_test",
        report_path=Path("outputs/client_test.md"),
        report="",
        body="",
        hitl="",
        facts={},
        data_dir=Path("data/client_test"),
        db_accounts=[],
    )
    defaults.update(kwargs)
    return ClientBundle(**defaults)


def test_fca_and_risk_pass() -> None:
    body = f"Intro\n\n{FCA_LINE}\n\n## Conclusion\n\n{RISK_WARNING}\n"
    b = _bundle(body=body, report=body)
    assert check_fca_line(b).passed
    assert check_risk_warning(b).passed


def test_tax_iff_selling() -> None:
    with_tax = "## Tax Implications\n\nCGT note\n\n## Fees\n"
    b_sell = _bundle(report=with_tax, body=with_tax, facts={"selling": True})
    b_nosell = _bundle(report="## Fees\n", body="## Fees\n", facts={"selling": False})
    assert check_tax_iff_selling(b_sell).passed
    assert check_tax_iff_selling(b_nosell).passed
    assert not check_tax_iff_selling(
        _bundle(report=with_tax, body=with_tax, facts={"selling": False})
    ).passed


def test_no_pounds_in_summary_ignores_table() -> None:
    report = (
        "## Background & Objectives\n\n"
        "Client is retired.\n\n"
        "| Account | Owner | Type | Value |\n"
        "|---------|-------|------|-------|\n"
        "| H-ISA-01 | X | ISA | £52,000 |\n"
    )
    b = _bundle(report=report, body=report)
    assert check_no_pounds_in_background_summary(b).passed

    bad = (
        "## Background & Objectives\n\n"
        "They will move £20,000 into the ISA.\n\n"
        "| Account | Owner | Type | Value |\n"
        "|---------|-------|------|-------|\n"
        "| H-ISA-01 | X | ISA | £52,000 |\n"
    )
    assert not check_no_pounds_in_background_summary(
        _bundle(report=bad, body=bad)
    ).passed


def test_history_delta_is_per_check_not_total() -> None:
    first = make_record(
        [
            CheckResult("client_01_clean", "fca_line", False, evidence="missing"),
            CheckResult("client_02_medium", "fca_line", True),
        ],
        run="baseline",
        note="C1 intro dropped the FCA line",
        recorded_at="2026-09-24T09:00:00",
    )
    second = make_record(
        [CheckResult("client_01_clean", "fca_line", True)],
        run="prompt-v3",
        note="C1 intro reads cleanly",
        recorded_at="2026-09-24T10:00:00",
    )
    delta = delta_against_history(second, [first])
    assert delta["improved"] == ["client_01_clean:fca_line"]
    assert delta["regressed"] == []
    text = format_history([first, second])
    assert "C1 intro reads cleanly" in text
    assert "+1 improved" in text
    assert "client_02_medium" in text
    eval_text = format_eval(
        [
            CheckResult("c", "sources_read", True, question="read"),
            CheckResult("c", "fact_shape", False, evidence="incomplete", question="facts"),
            CheckResult("c", "fca_line", True, question="section"),
            CheckResult("c", "tax_iff_selling", True, question="story"),
        ]
    )
    assert "## Read" in eval_text
    assert "## Facts" in eval_text
    assert "## Section" in eval_text
    assert "## Story" in eval_text
    assert "FAIL" in eval_text.split("## Section")[0]


def test_read_run_note_prefers_explicit_then_file(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("Background still mentions the transfer.\n", encoding="utf-8")
    assert read_run_note(tmp_path, "override") == "override"
    assert "transfer" in read_run_note(tmp_path, None)
    assert read_run_note(tmp_path / "missing", None) == ""


def test_table_account_ids() -> None:
    from eval.checks import check_table_account_ids

    report = (
        "## Background & Objectives\n\n"
        "| Account | Owner | Type | Value |\n"
        "|---------|-------|------|-------|\n"
        "| H-ISA-01 | A | ISA | £1 |\n"
        "| [REVIEW: accounts] | — | — | — |\n"
        "| New joint account | Joint | n/a | n/a |\n"
    )
    skipped = _bundle(report=report, db_accounts=[])
    assert check_table_account_ids(skipped).passed

    db = [
        {"account_id": "H-ISA-01", "status": "open"},
        {"account_id": "H-OLD", "status": "closed"},
    ]
    assert check_table_account_ids(_bundle(report=report, db_accounts=db)).passed

    duplicate = report + "| H-ISA-01 | A | ISA | £1 |\n"
    dup = check_table_account_ids(_bundle(report=duplicate, db_accounts=db))
    assert not dup.passed
    assert "duplicate H-ISA-01" in dup.evidence

    unknown = report + "| H-GIA-J | Joint | GIA | £2 |\n"
    bad_id = check_table_account_ids(_bundle(report=unknown, db_accounts=db))
    assert not bad_id.passed
    assert "unknown id H-GIA-J" in bad_id.evidence

    closed = report + "| H-OLD | A | ISA | £3 |\n"
    closed_row = check_table_account_ids(_bundle(report=closed, db_accounts=db))
    assert not closed_row.passed
    assert "closed H-OLD" in closed_row.evidence


def test_body_review_tags_must_reach_footer() -> None:
    from eval.checks import check_body_review_in_footer

    body = "Move the cash. [REVIEW: platform fee]\n"
    ok = _bundle(body=body, hitl="## Human review\n\n[REVIEW: platform fee]\n")
    assert check_body_review_in_footer(ok).passed

    plain = check_body_review_in_footer(
        _bundle(body=body, hitl="## Human review\n\nplatform fee\n")
    )
    assert not plain.passed
    assert "[REVIEW: platform fee]" in plain.evidence

    missing = check_body_review_in_footer(_bundle(body=body, hitl="## Human review\n\nNo items.\n"))
    assert not missing.passed
    assert "platform fee" in missing.evidence


def test_conflicts_surfaced() -> None:
    from eval.checks import check_conflicts_surfaced

    none = _bundle(facts={"conflicts": []}, hitl="### Conflicts\n\nNo open conflicts.\n")
    assert check_conflicts_surfaced(none).passed

    facts = {"conflicts": [{"field": "account_value:H-GIA-J", "details": "40k vs 45k"}]}
    empty = check_conflicts_surfaced(
        _bundle(facts=facts, hitl="### Conflicts\n\nNo open conflicts.\n### Sources\n")
    )
    assert not empty.passed

    listed = _bundle(
        facts=facts,
        hitl="### Conflicts\n\naccount_value:H-GIA-J\n### Sources\n",
    )
    assert check_conflicts_surfaced(listed).passed


def test_sources_read() -> None:
    from eval.checks import check_sources_read

    assert not check_sources_read(_bundle(facts={})).passed

    good = {
        "sources": [
            {"file": "photo.png", "role": "noise", "status": "skipped"},
            {"file": "req.docx", "role": "request", "status": "parsed"},
            {"file": "meet.docx", "role": "meeting", "status": "parsed"},
            {"file": "db.json", "role": "db", "status": "parsed"},
        ]
    }
    assert check_sources_read(_bundle(facts=good)).passed

    image = check_sources_read(
        _bundle(facts={"sources": [{"file": "photo.jpg", "role": "noise", "status": "parsed"}]})
    )
    assert not image.passed
    assert "not skipped" in image.evidence

    unparsed = check_sources_read(
        _bundle(facts={"sources": [{"file": "db.json", "role": "db", "status": "skipped"}]})
    )
    assert not unparsed.passed
    assert "not parsed" in unparsed.evidence


def test_fact_shape() -> None:
    from eval.checks import check_fact_shape

    assert not check_fact_shape(_bundle(facts={"facts": {}})).passed

    incomplete = check_fact_shape(
        _bundle(facts={"facts": [{"field": "selling", "id": "f-1", "source_file": "req"}]})
    )
    assert not incomplete.passed
    assert "incomplete selling" in incomplete.evidence

    bad_kind = check_fact_shape(
        _bundle(
            facts={
                "facts": [
                    {
                        "id": "f-1",
                        "field": "account_value",
                        "source_file": "db.json",
                        "excerpt": "x",
                        "kind": "guess",
                    }
                ]
            }
        )
    )
    assert not bad_kind.passed
    assert "bad kind guess" in bad_kind.evidence

    thin_conflict = check_fact_shape(
        _bundle(
            facts={
                "facts": [
                    {
                        "id": "f-1",
                        "field": "account_value",
                        "source_file": "db.json",
                        "excerpt": "x",
                        "kind": "account_balance",
                        "conflict": True,
                        "evidence": [{"source_file": "db.json"}],
                    }
                ]
            }
        )
    )
    assert not thin_conflict.passed
    assert "conflict missing evidence" in thin_conflict.evidence

    ok = {
        "facts": [
            {
                "id": "f-1",
                "field": "account_value",
                "source_file": "db.json",
                "excerpt": "account_balance H-ISA-01 = 52000",
                "kind": "account_balance",
                "conflict": True,
                "evidence": [{}, {}],
            }
        ]
    }
    assert check_fact_shape(_bundle(facts=ok)).passed


def test_actions_and_items() -> None:
    from eval.checks import check_actions_and_items

    facts = {
        "facts": [{"id": "f-transfer"}],
        "actions": [
            {"id": "a-fund-isa", "amount": 20000, "supports": "f-transfer"},
        ],
        "sections": {
            "recommendation": {
                "items": [{"action_id": "a-fund-isa", "text": "Invest £20,000 into the ISA."}]
            }
        },
    }
    assert check_actions_and_items(_bundle(facts=facts)).passed

    bad_id = check_actions_and_items(
        _bundle(
            facts={
                "facts": [{"id": "f-1"}],
                "actions": [{"id": "move", "supports": "f-1"}],
                "sections": {"recommendation": {"items": []}},
            }
        )
    )
    assert not bad_id.passed
    assert "bad id move" in bad_id.evidence

    with_kind = check_actions_and_items(
        _bundle(
            facts={
                "facts": [{"id": "f-1"}],
                "actions": [{"id": "a-1", "kind": "transfer_amount", "supports": "f-1"}],
                "sections": {"recommendation": {"items": [{"action_id": "a-1", "text": "Move it."}]}},
            }
        )
    )
    assert not with_kind.passed
    assert "has kind" in with_kind.evidence

    missing_support = check_actions_and_items(
        _bundle(
            facts={
                "facts": [],
                "actions": [{"id": "a-1", "supports": "f-missing"}],
                "sections": {"recommendation": {"items": [{"action_id": "a-1", "text": "Move it."}]}},
            }
        )
    )
    assert not missing_support.passed
    assert "supports missing" in missing_support.evidence

    mismatch = check_actions_and_items(
        _bundle(
            facts={
                "facts": [{"id": "f-transfer"}],
                "actions": [{"id": "a-fund-isa", "amount": 20000, "supports": "f-transfer"}],
                "sections": {
                    "recommendation": {
                        "items": [{"action_id": "a-fund-isa", "text": "Invest £80,000 into the GIA."}]
                    }
                },
            }
        )
    )
    assert not mismatch.passed
    assert "amount mismatch" in mismatch.evidence


def test_discover_and_scorecard_on_repo_outputs() -> None:
    root = Path(__file__).resolve().parents[1]
    outputs = root / "outputs"
    data = root / "data"
    if not any(outputs.glob("client_*.md")):
        return
    bundles = discover_clients(outputs, data)
    assert bundles
    results = run_checks(bundles)
    text = format_scorecard(results)
    assert "Eval scorecard" in text
    assert "|" in text
