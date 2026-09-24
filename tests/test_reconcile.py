"""Synthetic policy tests for observation reconciliation."""

from agent_pipeline.reconcile import (
    CGT_REVIEW_ITEM,
    amounts_match_action,
    build_case_document,
    reconcile_observations,
    validate_recommendation_items,
    values_materially_differ,
)


def _obs(field, value, role, *, as_of=None, account_id=None, source_file="x"):
    out = {
        "field": field,
        "value": value,
        "source_role": role,
        "source_file": source_file,
    }
    if as_of is not None:
        out["as_of"] = as_of
    if account_id is not None:
        out["account_id"] = account_id
    return out


def test_values_materially_differ_numeric() -> None:
    assert values_materially_differ(40000, 45000)
    assert not values_materially_differ(40000, 40000)
    assert not values_materially_differ(40000, "GBP 40,000")


def test_conflicting_dated_observations_create_conflict_and_pick_freshest() -> None:
    observations = [
        _obs("account_value", 40000, "db", as_of="2026-03-15", account_id="H-GIA-J"),
        _obs("account_value", 45000, "meeting", as_of="2026-05-14", account_id="H-GIA-J"),
    ]
    facts = reconcile_observations(observations)
    assert len(facts["conflicts"]) == 1
    assert facts["conflicts"][0]["field"] == "account_value:H-GIA-J"
    acc = next(a for a in facts["accounts"] if a["account_id"] == "H-GIA-J")
    assert acc["value"] == 45000
    assert acc["value_source"] == "meeting"
    assert acc["value_conflict"] is True
    assert acc.get("needs_review") is True


def test_reversing_dates_reverses_selected_observation() -> None:
    base = [
        _obs("account_value", 40000, "db", as_of="2026-03-15", account_id="H-GIA-J"),
        _obs("account_value", 45000, "meeting", as_of="2026-05-14", account_id="H-GIA-J"),
    ]
    forward = reconcile_observations(base)
    reversed_obs = [
        _obs("account_value", 40000, "db", as_of="2026-05-14", account_id="H-GIA-J"),
        _obs("account_value", 45000, "meeting", as_of="2026-03-15", account_id="H-GIA-J"),
    ]
    backward = reconcile_observations(reversed_obs)
    fwd = next(a for a in forward["accounts"] if a["account_id"] == "H-GIA-J")
    bwd = next(a for a in backward["accounts"] if a["account_id"] == "H-GIA-J")
    assert fwd["value"] == 45000
    assert bwd["value"] == 40000
    assert len(forward["conflicts"]) == 1
    assert len(backward["conflicts"]) == 1


def test_undated_disagreement_remains_review() -> None:
    observations = [
        _obs("account_value", 40000, "db", account_id="H-GIA-J"),
        _obs("account_value", 45000, "meeting", account_id="H-GIA-J"),
    ]
    facts = reconcile_observations(observations)
    assert len(facts["conflicts"]) == 1
    assert any("unresolvable" in item or "conflict" in item for item in facts["review_items"])
    # No defensible dated pick → value not forced from averaging
    acc = next(a for a in facts["accounts"] if a["account_id"] == "H-GIA-J")
    assert acc.get("value") is None or acc.get("needs_review") is True


def test_never_averages_conflicting_values() -> None:
    observations = [
        _obs("account_value", 30000, "db", as_of="2026-03-10", account_id="H-GIA-JF"),
        _obs("account_value", 38000, "meeting", as_of="2026-05-16", account_id="H-GIA-JF"),
    ]
    facts = reconcile_observations(observations)
    acc = next(a for a in facts["accounts"] if a["account_id"] == "H-GIA-JF")
    assert acc["value"] in {30000, 38000}
    assert acc["value"] != 34000


def test_null_account_value_becomes_review_item() -> None:
    observations = [
        _obs("account_value", None, "db", as_of="2026-04-30", account_id="H-CASH-JE"),
        _obs("account_type", "Cash Account", "db", account_id="H-CASH-JE"),
    ]
    facts = reconcile_observations(observations)
    assert any("H-CASH-JE" in item for item in facts["review_items"])
    acc = next(a for a in facts["accounts"] if a["account_id"] == "H-CASH-JE")
    assert acc["value"] is None
    assert acc.get("needs_review") is True


def test_request_authority_for_selling_and_fee_cgt_seeds() -> None:
    observations = [
        _obs("selling", True, "request"),
        _obs("accounts_covered", "ISAs and GIA", "request"),
        _obs("circumstances", "Retired", "meeting"),
    ]
    facts = reconcile_observations(observations)
    assert facts["selling"] is True
    assert facts["facts"]["accounts_covered"]["source"] == "request"
    assert facts["facts"]["circumstances"]["source"] == "meeting"
    assert "[REVIEW: platform fee]" in facts["review_items"]
    assert "[REVIEW: advice fee]" in facts["review_items"]
    assert CGT_REVIEW_ITEM in facts["review_items"]


def test_selling_false_skips_cgt_review_seed() -> None:
    facts = reconcile_observations([_obs("selling", False, "request")])
    assert facts["selling"] is False
    assert CGT_REVIEW_ITEM not in facts["review_items"]
    assert "[REVIEW: platform fee]" in facts["review_items"]


def test_text_conflict_on_request_field_keeps_request_draft() -> None:
    observations = [
        _obs("risk_profile", "4 (moderate)", "request"),
        _obs("risk_profile", "5 (balanced)", "meeting"),
    ]
    facts = reconcile_observations(observations)
    assert len(facts["conflicts"]) == 1
    assert facts["facts"]["risk_profile"]["value"] == "4 (moderate)"
    assert facts["facts"]["risk_profile"]["conflict"] is True


def test_phantom_meeting_account_id_does_not_create_row() -> None:
    observations = [
        _obs("account_value", 40000, "db", as_of="2026-03-15", account_id="H-GIA-J"),
        _obs("account_type", "GIA", "db", account_id="H-GIA-J"),
        _obs(
            "account_value",
            45000,
            "meeting",
            as_of="2026-05-14",
            account_id="joint_GIA",
        ),
        _obs(
            "account_value",
            20000,
            "meeting",
            as_of="2026-05-12",
            account_id=None,
        ),
    ]
    # Attach quotes like extract would
    observations[-1]["quote"] = "about twenty thousand in the cash account"
    observations[-2]["quote"] = "GIA looked nearer forty-five"
    facts = reconcile_observations(observations)
    ids = {a["account_id"] for a in facts["accounts"]}
    assert "joint_GIA" not in ids
    assert "H-GIA-J" in ids
    assert any("unmatched figure" in item for item in facts["review_items"])


def test_transfer_is_not_stored_as_account_balance() -> None:
    observations = [
        _obs("account_value", 25000, "db", as_of="2026-04-30", account_id="H-CASH-01", source_file="client_data_db.json"),
        _obs("account_value", 20000, "meeting", as_of="2026-05-12", account_id="H-CASH-01", source_file="meeting_notes.docx"),
        _obs("product", "David ISA", "request", source_file="report_request.docx"),
    ]
    observations[1]["kind"] = "transfer_amount"
    observations[1]["quote"] = "move £20,000 from the cash account"
    facts = reconcile_observations(observations)
    acc = next(a for a in facts["accounts"] if a["account_id"] == "H-CASH-01")
    assert acc["value"] == 25000
    case = build_case_document(
        facts, {"client_data_db.json": "db", "meeting_notes.docx": "meeting", "photo.png": "noise"}
    )
    assert any(row["file"] == "photo.png" and row["status"] == "skipped" for row in case["sources"])
    balance = next(f for f in case["facts"] if f["kind"] == "account_balance")
    transfer = next(f for f in case["facts"] if f["kind"] == "transfer_amount")
    assert balance["excerpt"] == "account_balance H-CASH-01 = 25000"
    assert transfer["excerpt"] == "move £20,000 from the cash account"
    assert transfer["conflict"] is False
    action = next(a for a in case["actions"] if a["supports"] == transfer["id"])
    assert action["id"].startswith("a-")
    assert "kind" not in action
    assert action["amount"] == 20000
    text, stored = validate_recommendation_items(
        {"items": [{"action_id": action["id"], "text": "Invest £80,000 into the GIA."}]},
        case["actions"],
    )
    assert "[REVIEW:" in text
    assert stored == []
    assert amounts_match_action("Invest £20,000 into David's ISA.", action["amount"])
    assert not amounts_match_action("Invest £80,000 into the GIA.", action["amount"])


def _transfer_case(observations: list[dict]) -> dict:
    for obs in observations:
        if obs["field"] == "account_value" and obs["source_role"] == "meeting":
            obs["kind"] = "transfer_amount"
        if obs["field"] == "investment_amount":
            obs["kind"] = "transfer_amount"
    return build_case_document(reconcile_observations(observations), {})


def test_request_amount_corroborates_one_transfer() -> None:
    case = _transfer_case(
        [
            _obs("account_value", 25000, "db", as_of="2026-04-30", account_id="H-CASH-01"),
            _obs("account_value", 20000, "meeting", as_of="2026-05-12", account_id="H-CASH-01"),
            _obs("investment_amount", "GBP 20,000", "request"),
            _obs("product", "Top up of existing Stocks & Shares ISA", "request"),
        ]
    )
    assert len(case["actions"]) == 1
    action = case["actions"][0]
    transfer = next(f for f in case["facts"] if f["field"] == "account_value" and f["kind"] == "transfer_amount")
    request = next(f for f in case["facts"] if f["field"] == "investment_amount")
    assert action["supports"] == transfer["id"]
    assert action["corroborated"] == request["id"]
    assert action["amount"] == 20000


def test_request_amount_collapses_only_the_matching_transfer() -> None:
    case = _transfer_case(
        [
            _obs("account_value", 25000, "db", as_of="2026-04-30", account_id="H-CASH-01"),
            _obs("account_value", 52000, "db", as_of="2026-04-30", account_id="H-ISA-01"),
            _obs("account_value", 20000, "meeting", as_of="2026-05-12", account_id="H-CASH-01"),
            _obs("account_value", 50000, "meeting", as_of="2026-05-12", account_id="H-ISA-01"),
            _obs("investment_amount", "GBP 20,000", "request"),
            _obs("product", "Top up", "request"),
        ]
    )
    assert len(case["actions"]) == 2
    corroborated = [a for a in case["actions"] if a.get("corroborated")]
    assert len(corroborated) == 1
    assert corroborated[0]["amount"] == 20000
    assert any(a["amount"] == 50000 and "corroborated" not in a for a in case["actions"])


def test_same_amount_on_two_transfers_stays_separate() -> None:
    case = _transfer_case(
        [
            _obs("account_value", 25000, "db", as_of="2026-04-30", account_id="H-CASH-01"),
            _obs("account_value", 52000, "db", as_of="2026-04-30", account_id="H-ISA-01"),
            _obs("account_value", 20000, "meeting", as_of="2026-05-12", account_id="H-CASH-01"),
            _obs("account_value", 20000, "meeting", as_of="2026-05-12", account_id="H-ISA-01"),
            _obs("investment_amount", "GBP 20,000", "request"),
            _obs("product", "Top up", "request"),
        ]
    )
    assert len(case["actions"]) == 3
    assert all("corroborated" not in action for action in case["actions"])
