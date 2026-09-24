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
