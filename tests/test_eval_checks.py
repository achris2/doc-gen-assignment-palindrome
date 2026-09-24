"""Smoke tests for deterministic report eval."""

from pathlib import Path

from eval.checks import (
    FCA_LINE,
    RISK_WARNING,
    ClientBundle,
    check_fca_line,
    check_no_pounds_in_background_summary,
    check_risk_warning,
    check_tax_iff_selling,
    discover_clients,
    format_scorecard,
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
