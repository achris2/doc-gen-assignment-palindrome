"""Lightweight deterministic eval over generated reports.

Usage:
    python -m eval.checks
    python -m eval.checks --outputs-dir outputs/runs/prompt-v3 --record

Write a qualitative note at outputs/runs/prompt-v3/notes.md before recording.
The history log compares each check with the last time that client and check
were scored, so a one-client run is not judged against a four-client total.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
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
    question: str = "story"


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
        facts_path = outputs_dir / name / "case_facts.json"
        if not facts_path.exists():
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


def _tagged(result: CheckResult, question: str) -> CheckResult:
    result.question = question
    return result


def check_sources_read(bundle: ClientBundle) -> CheckResult:
    sources = bundle.facts.get("sources") or []
    if not sources:
        return CheckResult(
            client=bundle.name,
            check="sources_read",
            passed=False,
            question="read",
            evidence="No sources on case_facts.json",
            expected="Core files parsed; images skipped",
        )
    problems = []
    for row in sources:
        file_name = str(row.get("file") or "")
        status = row.get("status")
        if file_name.lower().endswith((".png", ".jpg", ".jpeg")) and status != "skipped":
            problems.append(f"{file_name} not skipped")
        if row.get("role") in {"request", "meeting", "db"} and status != "parsed":
            problems.append(f"{file_name} not parsed")
    return CheckResult(
        client=bundle.name,
        check="sources_read",
        passed=not problems,
        question="read",
        evidence="" if not problems else "; ".join(problems),
        expected="Core files parsed; images skipped",
    )


def check_fact_shape(bundle: ClientBundle) -> CheckResult:
    facts = bundle.facts.get("facts")
    if not isinstance(facts, list):
        return CheckResult(
            client=bundle.name,
            check="fact_shape",
            passed=False,
            question="facts",
            evidence="facts is not a list",
            expected="Each fact has id, source file, excerpt, and kind when monetary",
        )
    problems = []
    for fact in facts:
        if not fact.get("id") or not fact.get("source_file") or not fact.get("excerpt"):
            problems.append(f"incomplete {fact.get('field')}")
        kind = fact.get("kind")
        if kind and kind not in {
            "account_balance",
            "transfer_amount",
            "received_proceeds",
            "loan_repayment",
            "contingent_proceeds",
        }:
            problems.append(f"bad kind {kind}")
        if fact.get("conflict") and len(fact.get("evidence") or []) < 2:
            problems.append(f"{fact.get('id')} conflict missing evidence")
    return CheckResult(
        client=bundle.name,
        check="fact_shape",
        passed=not problems,
        question="facts",
        evidence="" if not problems else "; ".join(problems),
        expected="Each fact has id, source file, excerpt, and kind when monetary",
    )


def check_actions_and_items(bundle: ClientBundle) -> CheckResult:
    from agent_pipeline.reconcile import amounts_match_action

    actions = {a.get("id"): a for a in bundle.facts.get("actions") or []}
    fact_ids = {f.get("id") for f in bundle.facts.get("facts") or [] if isinstance(f, dict)}
    problems = []
    for action in actions.values():
        if not str(action.get("id") or "").startswith("a-"):
            problems.append(f"bad id {action.get('id')}")
        if "kind" in action:
            problems.append(f"{action.get('id')} has kind")
        if action.get("supports") not in fact_ids:
            problems.append(f"{action.get('id')} supports missing")
    items = ((bundle.facts.get("sections") or {}).get("recommendation") or {}).get("items") or []
    seen = [item.get("action_id") for item in items]
    if actions and seen != list(actions):
        problems.append("recommendation items are not one per action")
    for item in items:
        action = actions.get(item.get("action_id"))
        if action and not amounts_match_action(str(item.get("text") or ""), action.get("amount")):
            problems.append(f"{item.get('action_id')} amount mismatch")
    return CheckResult(
        client=bundle.name,
        check="action_items",
        passed=not problems,
        question="section",
        evidence="" if not problems else "; ".join(problems),
        expected="One item per action and each £ matches that action only",
    )


NARRATIVE_FIELDS = frozenset({"circumstances", "objectives", "recommendation_summary"})


def _fact_rows(bundle: ClientBundle) -> list[dict[str, Any]]:
    facts = bundle.facts.get("facts")
    if not isinstance(facts, list):
        return []
    return [row for row in facts if isinstance(row, dict)]


def _action_rows(bundle: ClientBundle) -> list[dict[str, Any]]:
    actions = bundle.facts.get("actions")
    if not isinstance(actions, list):
        return []
    return [row for row in actions if isinstance(row, dict)]


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)", value)
        if not match:
            return None
        return float(match.group(1).replace(",", ""))
    return None


def _case(bundle: ClientBundle, check: str, problems: list[str], expected: str) -> CheckResult:
    return CheckResult(
        client=bundle.name,
        check=check,
        passed=not problems,
        question="case",
        evidence="" if not problems else "; ".join(problems),
        expected=expected,
    )


def check_narrative_not_action(bundle: ClientBundle) -> CheckResult:
    """A narrative field is not a money event and must not become an action."""
    facts = _fact_rows(bundle)
    by_id = {row.get("id"): row for row in facts}
    problems: list[str] = []
    for fact in facts:
        if fact.get("field") in NARRATIVE_FIELDS and fact.get("kind"):
            problems.append(f"{fact.get('field')} kind {fact.get('kind')}")
    for action in _action_rows(bundle):
        support = by_id.get(action.get("supports")) or {}
        if support.get("field") not in NARRATIVE_FIELDS:
            continue
        if action.get("amount") is None and action.get("amount_status") == "not_agreed":
            continue
        problems.append(f"{action.get('id')} from {support.get('field')}")
    return _case(
        bundle,
        "narrative_not_action",
        problems,
        "Narrative fields have no money kind and do not generate actions",
    )


_FLOW_KINDS = frozenset({"received_proceeds", "loan_repayment", "contingent_proceeds"})


def check_action_amount_numeric(bundle: ClientBundle) -> CheckResult:
    from agent_pipeline.reconcile import _to_number

    facts = {row.get("id"): row for row in _fact_rows(bundle)}
    problems: list[str] = []
    for action in _action_rows(bundle):
        if action.get("amount") is None and action.get("amount_status") == "not_agreed":
            continue
        if _to_number(action.get("amount")) is None:
            problems.append(f"{action.get('id')} amount {action.get('amount')}")
        kind = (facts.get(action.get("supports")) or {}).get("kind")
        if kind != "transfer_amount":
            problems.append(f"{action.get('id')} supports {kind}")
    return _case(
        bundle,
        "action_amount_numeric",
        problems,
        "Every action amount is a number backed by a transfer",
    )


def check_money_kinds_distinct(bundle: ClientBundle) -> CheckResult:
    """The same amount cannot be both a receipt and a repayment, or contingent."""
    by_amount: dict[float, set[str]] = {}
    for fact in _fact_rows(bundle):
        if fact.get("conflict"):
            continue
        kind = fact.get("kind")
        number = _as_number(fact.get("value"))
        if kind not in _FLOW_KINDS or number is None:
            continue
        by_amount.setdefault(number, set()).add(kind)
    problems = [
        f"{int(amount) if amount == int(amount) else amount} kinds {', '.join(sorted(kinds))}"
        for amount, kinds in sorted(by_amount.items())
        if len(kinds) > 1
    ]
    return _case(
        bundle,
        "money_kinds_distinct",
        problems,
        "A receipt, repayment, and contingent amount are different facts",
    )


def check_contingent_not_action(bundle: ClientBundle) -> CheckResult:
    facts = {row.get("id"): row for row in _fact_rows(bundle)}
    problems = [
        f"{action.get('id')} supports contingent_proceeds"
        for action in _action_rows(bundle)
        if (facts.get(action.get("supports")) or {}).get("kind") == "contingent_proceeds"
    ]
    return _case(
        bundle,
        "contingent_not_action",
        problems,
        "Contingent proceeds are facts and do not become actions",
    )


def _meeting_text(bundle: ClientBundle) -> str:
    sources = bundle.facts.get("sources") or []
    name = next((row.get("file") for row in sources if isinstance(row, dict) and row.get("role") == "meeting"), None)
    if not name:
        return ""
    path = bundle.data_dir / str(name)
    if not path.exists():
        return ""
    from document_formatter.loading import read_file

    return read_file(path)


def _has_amount(facts: list[dict[str, Any]], amount: float) -> bool:
    for fact in facts:
        number = _as_number(fact.get("value"))
        if number is not None and abs(number - amount) <= 0.01:
            return True
    return False


def check_meeting_pounds_covered(bundle: ClientBundle) -> CheckResult:
    from agent_pipeline.reconcile import pound_amounts

    missing = [
        str(int(amount) if amount == int(amount) else amount)
        for amount in pound_amounts(_meeting_text(bundle))
        if not _has_amount(_fact_rows(bundle), amount)
    ]
    return _case(
        bundle,
        "meeting_pounds_covered",
        [f"£{token} missing" for token in missing],
        "Every £ amount in the meeting note is a numeric fact value",
    )


def check_selling_has_dispose(bundle: ClientBundle) -> CheckResult:
    facts = _fact_rows(bundle)
    selling = any(row.get("field") == "selling" and row.get("value") is True for row in facts)
    if not selling:
        return _case(bundle, "selling_has_dispose", [], "selling requires a supported disposal decision")
    by_id = {row.get("id"): row for row in facts}
    linked = False
    for decision in bundle.facts.get("decisions") or []:
        if not isinstance(decision, dict) or decision.get("type") != "dispose":
            continue
        if decision.get("amount") not in (None,):
            continue
        support = by_id.get(decision.get("supports")) or {}
        if support.get("field") == "dispose" and support.get("excerpt") and not support.get("kind"):
            linked = True
    return _case(
        bundle,
        "selling_has_dispose",
        [] if linked else ["selling=true without a supported disposal decision"],
        "selling requires a supported disposal decision",
    )


def run_checks(bundles: list[ClientBundle]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for bundle in bundles:
        results.append(check_sources_read(bundle))
        results.append(check_fact_shape(bundle))
        results.append(_tagged(check_fca_line(bundle), "section"))
        results.append(_tagged(check_risk_warning(bundle), "section"))
        results.append(check_actions_and_items(bundle))
        results.append(_tagged(check_no_pounds_in_background_summary(bundle), "section"))
        results.append(_tagged(check_body_review_in_footer(bundle), "section"))
        results.append(_tagged(check_tax_iff_selling(bundle), "story"))
        results.append(_tagged(check_table_account_ids(bundle), "story"))
        results.append(_tagged(check_conflicts_surfaced(bundle), "story"))
        results.append(check_narrative_not_action(bundle))
        results.append(check_action_amount_numeric(bundle))
        results.append(check_contingent_not_action(bundle))
        results.append(check_money_kinds_distinct(bundle))
        results.append(check_meeting_pounds_covered(bundle))
        results.append(check_selling_has_dispose(bundle))
    return results


def question_counts(results: list[CheckResult]) -> dict[str, dict[str, int]]:
    buckets: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = buckets.setdefault(result.question, {"passed": 0, "total": 0})
        bucket["total"] += 1
        if result.passed:
            bucket["passed"] += 1
    return buckets


def format_eval(results: list[CheckResult]) -> str:
    """Four headings. No single averaged score."""
    counts = question_counts(results)
    lines = ["# Eval", ""]
    for question in ("read", "facts", "section", "story", "case"):
        bucket = counts.get(question, {"passed": 0, "total": 0})
        lines.append(f"## {question.title()}")
        lines.append("")
        lines.append(f"{bucket['passed']}/{bucket['total']} passing")
        lines.append("")
        for result in results:
            if result.question != question:
                continue
            mark = "PASS" if result.passed else "FAIL"
            evidence = (result.evidence or "").replace("\n", " ")
            lines.append(f"- {result.client} `{result.check}` {mark} {evidence}".rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# USD per 1,000,000 tokens. An estimate, not an invoice.
RATE_CARD_AS_OF = "2026-09-01"
RATE_CARD = {
    "gpt-4o-mini": {"prompt": 0.15, "cached": 0.075, "completion": 0.60},
}


def estimate_usd(calls: list[dict[str, Any]]) -> float | None:
    total = 0.0
    priced = False
    for call in calls:
        rates = RATE_CARD.get(str(call.get("model") or ""))
        if rates is None:
            continue
        priced = True
        prompt = int(call.get("prompt_tokens") or 0)
        cached = int(call.get("cached_tokens") or 0)
        completion = int(call.get("completion_tokens") or 0)
        total += max(prompt - cached, 0) / 1_000_000 * rates["prompt"]
        total += cached / 1_000_000 * rates["cached"]
        total += completion / 1_000_000 * rates["completion"]
    if not priced:
        return None
    return round(total, 6)


def format_usage(outputs_dir: Path) -> str:
    """A cost note beside the scorecard. It is not a check and does not change the exit code."""
    files = sorted(outputs_dir.glob("*.usage.json"))
    if not files:
        return ""
    lines = [
        "## Usage",
        "",
        f"Estimated USD using the {RATE_CARD_AS_OF} rate card. Not a billing record.",
        "",
    ]
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        calls = data.get("calls") or []
        prompt = sum(int(call.get("prompt_tokens") or 0) for call in calls)
        completion = sum(int(call.get("completion_tokens") or 0) for call in calls)
        cost = estimate_usd(calls)
        cost_text = "unpriced" if cost is None else f"${cost:.4f}"
        lines.append(
            f"- {path.name}: {len(calls)} calls, {prompt} prompt tokens, "
            f"{completion} completion tokens, pipeline {data.get('pipeline_ms')} ms, {cost_text}"
        )
    if len(lines) == 4:
        return ""
    return "\n".join(lines).rstrip() + "\n"


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


def make_record(
    results: list[CheckResult],
    *,
    run: str,
    note: str = "",
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """One history row: scores plus the qualitative note for this run."""
    clients = sorted({r.client for r in results})
    return {
        "run": run,
        "recorded_at": recorded_at or datetime.now().isoformat(timespec="seconds"),
        "note": note.strip(),
        "clients": clients,
        "passed": sum(1 for r in results if r.passed),
        "total": len(results),
        "questions": question_counts(results),
        "checks": [
            {
                "client": r.client,
                "check": r.check,
                "passed": r.passed,
                "question": r.question,
                "evidence": r.evidence,
            }
            for r in results
        ],
    }


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_history(record: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _latest_prior(
    history: list[dict[str, Any]], client: str, check: str
) -> bool | None:
    for record in reversed(history):
        for item in record.get("checks") or []:
            if item.get("client") == client and item.get("check") == check:
                return bool(item.get("passed"))
    return None


def delta_against_history(
    record: dict[str, Any], history: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """Compare this run with the last time each client/check was scored."""
    improved: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    new: list[str] = []
    for item in record.get("checks") or []:
        label = f"{item['client']}:{item['check']}"
        prior = _latest_prior(history, item["client"], item["check"])
        passed = bool(item.get("passed"))
        if prior is None:
            new.append(label)
        elif prior and not passed:
            regressed.append(label)
        elif not prior and passed:
            improved.append(label)
        else:
            unchanged.append(label)
    return {
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "new": new,
    }


def read_run_note(outputs_dir: Path, note: str | None = None) -> str:
    """Prefer an explicit note, then notes.md in the run directory."""
    if note and note.strip():
        return note.strip()
    path = outputs_dir / "notes.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def format_delta(delta: dict[str, list[str]]) -> str:
    parts = [
        f"+{len(delta['improved'])} improved",
        f"-{len(delta['regressed'])} regressed",
        f"{len(delta['new'])} new",
    ]
    return ", ".join(parts)


def format_history(history: list[dict[str, Any]]) -> str:
    """Markdown log: one row per run, then the note and check movement."""
    lines = [
        "# Eval history",
        "",
        "Each row is one generation. Delta is against the previous time that same client and check were scored.",
        "",
        "| Run | When | Read | Facts | Section | Story | Case | Delta | Qualitative |",
        "|-----|------|------|-------|---------|-------|------|-------|-------------|",
    ]
    prior: list[dict[str, Any]] = []
    details: list[str] = []
    for record in history:
        delta = delta_against_history(record, prior)
        questions = record.get("questions") or {}
        def _cell(name: str) -> str:
            bucket = questions.get(name) or {}
            if not bucket:
                return ""
            return f"{bucket.get('passed', 0)}/{bucket.get('total', 0)}"
        note = (record.get("note") or "").replace("\n", " ").replace("|", "\\|")
        if len(note) > 120:
            note = note[:117] + "..."
        clients = ", ".join(record.get("clients") or [])
        lines.append(
            f"| {record.get('run', '')} ({clients}) | {record.get('recorded_at', '')} | "
            f"{_cell('read')} | {_cell('facts')} | {_cell('section')} | {_cell('story')} | {_cell('case')} | "
            f"{format_delta(delta)} | {note} |"
        )
        moved = []
        if delta["improved"]:
            moved.append("Improved: " + ", ".join(delta["improved"]))
        if delta["regressed"]:
            moved.append("Regressed: " + ", ".join(delta["regressed"]))
        if moved or record.get("note"):
            details.append(f"## {record.get('run', '')}")
            details.append("")
            if record.get("note"):
                details.append(record["note"].strip())
                details.append("")
            if moved:
                details.extend(f"- {line}" for line in moved)
                details.append("")
        prior.append(record)
    lines.append("")
    lines.extend(details)
    return "\n".join(lines).rstrip() + "\n"


def write_history_markdown(history: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_history(history), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic report checks.")
    parser.add_argument("--outputs-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--scorecard",
        type=Path,
        default=None,
        help="Defaults to <outputs-dir>/scorecard.md when --record is set, else outputs/eval/scorecard.md",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Append this run to the history log, including failures",
    )
    parser.add_argument(
        "--run",
        default=None,
        help="Run name recorded in the history. Defaults to the outputs directory name.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Qualitative note. If omitted, notes.md in the outputs directory is used.",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=Path("outputs/eval/history.jsonl"),
    )
    parser.add_argument(
        "--history-md",
        type=Path,
        default=Path("outputs/eval/history.md"),
    )
    args = parser.parse_args()

    bundles = discover_clients(args.outputs_dir, args.data_dir)
    if not bundles:
        print(f"No client_*.md reports under {args.outputs_dir}")
        raise SystemExit(1)
    results = run_checks(bundles)
    eval_path = args.outputs_dir / "eval.md" if args.record else Path("outputs/eval/eval.md")
    if args.scorecard is not None:
        eval_path = args.scorecard
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    text = format_eval(results)
    usage = format_usage(args.outputs_dir)
    if usage:
        text = text.rstrip() + "\n\n" + usage
    eval_path.write_text(text, encoding="utf-8")
    counts = question_counts(results)
    print(f"Wrote {eval_path}")
    for name in ("read", "facts", "section", "story", "case"):
        bucket = counts.get(name, {"passed": 0, "total": 0})
        print(f"{name}: {bucket['passed']}/{bucket['total']}")

    if args.record:
        history = load_history(args.history)
        record = make_record(
            results,
            run=args.run or args.outputs_dir.name,
            note=read_run_note(args.outputs_dir, args.note),
        )
        run_meta = next((b.facts.get("run") or {} for b in bundles if b.facts.get("run")), {})
        if run_meta:
            record["model"] = run_meta.get("model")
            record["config_sha"] = run_meta.get("config_sha")
            record["git_rev"] = run_meta.get("git_rev")
        delta = delta_against_history(record, history)
        append_history(record, args.history)
        history.append(record)
        md_path = write_history_markdown(history, args.history_md)
        print(f"Recorded {record['run']} ({format_delta(delta)})")
        print(f"Wrote {args.history}")
        print(f"Wrote {md_path}")
        if delta["regressed"]:
            print("Regressed: " + ", ".join(delta["regressed"]))
        if delta["improved"]:
            print("Improved: " + ", ".join(delta["improved"]))

    failed = [r for r in results if not r.passed]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
