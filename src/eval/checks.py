"""Lightweight deterministic eval over generated reports.

Usage:
    python -m eval.checks
    python -m eval.checks --outputs-dir outputs --data-dir data
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FCA_LINE = "This firm is authorised and regulated by the Financial Conduct Authority."
RISK_WARNING = (
    "The value of investments can fall as well as rise and you may get back less "
    "than you invest. Past performance is not a guide to future returns."
)

_HITL_SPLIT = re.compile(r"^## Human review\s*$", re.MULTILINE)
_SECTION = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_REVIEW_TAG = re.compile(r"\[REVIEW:[^\]]+\]")
_TABLE_ROW = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$"
)


@dataclass
class CheckResult:
    client: str
    check: str
    passed: bool
    evidence: str = ""
    expected: str = ""


@dataclass
class ClientBundle:
    name: str
    report_path: Path
    report: str
    body: str
    hitl: str
    facts: dict[str, Any]
    data_dir: Path
    db_accounts: list[dict[str, Any]] = field(default_factory=list)


def _split_hitl(report: str) -> tuple[str, str]:
    match = _HITL_SPLIT.search(report)
    if not match:
        return report, ""
    return report[: match.start()].rstrip(), report[match.start() :].lstrip()


def _section_body(report: str, title: str) -> str | None:
    headings = list(_SECTION.finditer(report))
    for i, match in enumerate(headings):
        if match.group(1).strip() == title:
            start = match.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(report)
            return report[start:end].strip()
    return None


def _background_summary(report: str) -> str:
    """Background prose only — stop before the holdings table."""
    section = _section_body(report, "Background & Objectives") or ""
    table_at = re.search(r"^\|", section, re.MULTILINE)
    if table_at:
        return section[: table_at.start()].strip()
    return section


def _parse_holdings_rows(report: str) -> list[tuple[str, str, str, str]]:
    section = _section_body(report, "Background & Objectives") or ""
    rows: list[tuple[str, str, str, str]] = []
    for line in section.splitlines():
        match = _TABLE_ROW.match(line.strip())
        if not match:
            continue
        cells = tuple(c.strip() for c in match.groups())
        if cells[0].lower() in {"account", "---------", "---"} or set(cells[0]) <= {"-"}:
            continue
        if cells[0].startswith("---"):
            continue
        rows.append(cells)  # type: ignore[arg-type]
    return rows


def _load_db_accounts(data_dir: Path) -> list[dict[str, Any]]:
    db_path = data_dir / "client_data_db.json"
    if not db_path.exists():
        return []
    data = json.loads(db_path.read_text(encoding="utf-8"))
    by_id: dict[str, dict[str, Any]] = {}
    holders = data.get("holders") or {}
    if not isinstance(holders, dict):
        return []
    for holder in holders.values():
        if not isinstance(holder, dict):
            continue
        for account in holder.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            aid = account.get("account_id")
            if aid and aid not in by_id:
                by_id[str(aid)] = dict(account)
    return list(by_id.values())


def discover_clients(outputs_dir: Path, data_dir: Path) -> list[ClientBundle]:
    bundles: list[ClientBundle] = []
    for path in sorted(outputs_dir.glob("client_*.md")):
        name = path.stem
        facts_path = outputs_dir / name / "facts.json"
        facts: dict[str, Any] = {}
        if facts_path.exists():
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
        client_data = data_dir / name
        report = path.read_text(encoding="utf-8")
        body, hitl = _split_hitl(report)
        bundles.append(
            ClientBundle(
                name=name,
                report_path=path,
                report=report,
                body=body,
                hitl=hitl,
                facts=facts,
                data_dir=client_data,
                db_accounts=_load_db_accounts(client_data)
                if client_data.exists()
                else [],
            )
        )
    return bundles


def check_fca_line(bundle: ClientBundle) -> CheckResult:
    ok = FCA_LINE in bundle.body
    return CheckResult(
        client=bundle.name,
        check="fca_line",
        passed=ok,
        evidence="" if ok else "FCA line missing from report body",
        expected=FCA_LINE,
    )


def check_risk_warning(bundle: ClientBundle) -> CheckResult:
    ok = RISK_WARNING in bundle.body
    return CheckResult(
        client=bundle.name,
        check="risk_warning",
        passed=ok,
        evidence="" if ok else "Risk warning missing or paraphrased",
        expected=RISK_WARNING,
    )


def check_tax_iff_selling(bundle: ClientBundle) -> CheckResult:
    selling = bool(bundle.facts.get("selling"))
    tax = _section_body(bundle.report, "Tax Implications")
    has_tax = tax is not None
    ok = has_tax == selling
    return CheckResult(
        client=bundle.name,
        check="tax_iff_selling",
        passed=ok,
        evidence=f"selling={selling}, tax_section_present={has_tax}",
        expected="Tax section present iff selling is true",
    )


def check_no_pounds_in_background_summary(bundle: ClientBundle) -> CheckResult:
    summary = _background_summary(bundle.report)
    hits = re.findall(r"£\s*[\d,]+(?:\.\d+)?", summary)
    ok = len(hits) == 0
    return CheckResult(
        client=bundle.name,
        check="no_pounds_in_background_summary",
        passed=ok,
        evidence="" if ok else f"Found figures in summary: {hits}",
        expected="No £ amounts in Background summary prose",
    )


def check_table_account_ids(bundle: ClientBundle) -> CheckResult:
    """Table account IDs exist in db, are not closed, and appear once (skip synthetic/review)."""
    if not bundle.db_accounts:
        return CheckResult(
            client=bundle.name,
            check="table_account_ids",
            passed=True,
            evidence="No db accounts loaded; skipped",
            expected="Table ids ⊆ db, not closed, unique",
        )
    db_by_id = {str(a["account_id"]): a for a in bundle.db_accounts}
    rows = _parse_holdings_rows(bundle.report)
    seen: set[str] = set()
    problems: list[str] = []
    for aid, *_ in rows:
        if aid.startswith("[REVIEW") or aid in {"—", "-"}:
            continue
        if "new" in aid.lower():
            continue
        if aid in seen:
            problems.append(f"duplicate {aid}")
            continue
        seen.add(aid)
        db_acc = db_by_id.get(aid)
        if db_acc is None:
            problems.append(f"unknown id {aid}")
            continue
        status = str(db_acc.get("status") or "").lower()
        if status == "closed":
            problems.append(f"closed {aid}")
    ok = not problems
    return CheckResult(
        client=bundle.name,
        check="table_account_ids",
        passed=ok,
        evidence="" if ok else "; ".join(problems),
        expected="Table ids ⊆ db, not closed, unique",
    )


def check_body_review_in_footer(bundle: ClientBundle) -> CheckResult:
    body_tags = set(_REVIEW_TAG.findall(bundle.body))
    footer_tags = set(_REVIEW_TAG.findall(bundle.hitl))
    # Also accept tags listed as plain review item lines without requiring exact set equality
    # of every footer-only tag.
    missing = body_tags - footer_tags
    # Footer may list tags without brackets in rare cases — require tag text in hitl
    still_missing = [t for t in missing if t not in bundle.hitl]
    ok = not still_missing
    return CheckResult(
        client=bundle.name,
        check="body_review_subset_footer",
        passed=ok,
        evidence="" if ok else f"Missing from footer: {still_missing}",
        expected="Every body [REVIEW] appears in Human review footer",
    )


def check_conflicts_surfaced(bundle: ClientBundle) -> CheckResult:
    """If facts record account value conflicts, footer Conflicts must not say none."""
    conflicts = bundle.facts.get("conflicts") or []
    account_conflicts = [
        c
        for c in conflicts
        if str(c.get("field", "")).startswith("account_value:")
    ]
    if not account_conflicts:
        return CheckResult(
            client=bundle.name,
            check="conflicts_surfaced",
            passed=True,
            evidence="No account_value conflicts in facts.json",
            expected="Conflicts section lists open account value conflicts",
        )
    hitl_conflicts = _section_body(bundle.hitl, "Conflicts") or bundle.hitl
    # After HITL split, Conflicts is ### under Human review — search hitl text
    if "### Conflicts" in bundle.hitl:
        part = bundle.hitl.split("### Conflicts", 1)[1]
        part = part.split("###", 1)[0]
        hitl_conflicts = part
    empty = "no open conflicts" in hitl_conflicts.lower()
    ok = not empty and len(hitl_conflicts.strip()) > 0
    return CheckResult(
        client=bundle.name,
        check="conflicts_surfaced",
        passed=ok,
        evidence=""
        if ok
        else f"facts has {len(account_conflicts)} account conflicts but footer empty",
        expected="Conflicts section lists open account value conflicts",
    )


def run_checks(bundles: list[ClientBundle]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for bundle in bundles:
        results.append(check_fca_line(bundle))
        results.append(check_risk_warning(bundle))
        results.append(check_tax_iff_selling(bundle))
        results.append(check_no_pounds_in_background_summary(bundle))
        results.append(check_table_account_ids(bundle))
        results.append(check_body_review_in_footer(bundle))
        results.append(check_conflicts_surfaced(bundle))
    return results


def format_scorecard(results: list[CheckResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines = [
        "# Eval scorecard",
        "",
        f"**{passed}/{total} passing**",
        "",
        "| Client | Check | Result | Evidence |",
        "|--------|-------|--------|----------|",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        evidence = (r.evidence or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.client} | {r.check} | {mark} | {evidence} |")
    lines.append("")
    fails = [r for r in results if not r.passed]
    if fails:
        lines.extend(["## Failures detail", ""])
        for r in fails:
            lines.append(f"### {r.client} — `{r.check}`")
            lines.append(f"- Expected: {r.expected}")
            lines.append(f"- Evidence: {r.evidence or '(none)'}")
            lines.append("")
    return "\n".join(lines)


def write_scorecard(results: list[CheckResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_scorecard(results), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic report checks.")
    parser.add_argument("--outputs-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--scorecard",
        type=Path,
        default=Path("outputs/eval/scorecard.md"),
    )
    args = parser.parse_args()

    bundles = discover_clients(args.outputs_dir, args.data_dir)
    if not bundles:
        print(f"No client_*.md reports under {args.outputs_dir}")
        raise SystemExit(1)
    results = run_checks(bundles)
    out = write_scorecard(results, args.scorecard)
    passed = sum(1 for r in results if r.passed)
    print(f"Wrote {out} ({passed}/{len(results)} passing)")
    if passed < len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
