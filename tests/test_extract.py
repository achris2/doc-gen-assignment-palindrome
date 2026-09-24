"""Tests for deterministic + LLM-fallback observation extraction."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from agent_pipeline.extract import (
    extract_db_observations,
    extract_meeting_observations,
    extract_observations,
    extract_request_observations,
    parse_db_accounts,
    parse_request_kv_lines,
)


def _mock_llm(payload: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))]
    )
    return client


def test_parse_request_kv_lines() -> None:
    text = (
        "Report Requirement Summary\n"
        "Accounts covered | Holloway Stocks & Shares ISA\n"
        "Selling existing investments? | No\n"
        "Product recommended | Top up\n"
    )
    pairs = parse_request_kv_lines(text)
    assert pairs["accounts covered"] == "Holloway Stocks & Shares ISA"
    assert pairs["selling existing investments?"] == "No"


def test_extract_request_observations_deterministic() -> None:
    text = (
        "Accounts covered | Holloway ISA\n"
        "Investment amount | GBP 20,000\n"
        "Source of funds | Cash account\n"
        "Selling existing investments? | No\n"
        "Product recommended | Top up\n"
        "Held in single or joint name? | Single\n"
        "Agreed risk profile | 4 (moderate)\n"
        "Initial charge | 0%\n"
    )
    obs = extract_request_observations(text, "report_request.docx")
    by_field = {o.field: o for o in obs}
    assert by_field["selling"].value is False
    assert by_field["accounts_covered"].value == "Holloway ISA"
    assert by_field["investment_amount"].value == "GBP 20,000"
    assert by_field["initial_charge"].value == "0%"
    assert all(o.source_role == "request" for o in obs)


def test_extract_request_selling_with_parenthetical() -> None:
    text = "Selling existing investments? | Yes (partial rebalance of the Holloway joint GIA)\n"
    obs = extract_request_observations(text, "report_request.docx")
    assert len(obs) == 1
    assert obs[0].field == "selling"
    assert obs[0].value is True


def test_extract_request_fallback_when_unrecognized() -> None:
    client = _mock_llm(
        {
            "observations": [
                {"field": "accounts_covered", "value": "ISA", "account_id": None, "as_of": None},
                {"field": "selling", "value": "yes", "account_id": None, "as_of": None},
            ]
        }
    )
    obs = extract_request_observations(
        "Please cover the ISA and sell the GIA.",
        "odd_request.txt",
        openai_client=client,
        model="test",
    )
    by_field = {o.field: o for o in obs}
    assert by_field["selling"].value is True
    assert by_field["accounts_covered"].value == "ISA"
    assert client.chat.completions.create.called


def test_parse_db_dedupes_joint_accounts() -> None:
    data = {
        "snapshot_date": "2026-04-30",
        "holders": {
            "client": {
                "name": "A",
                "accounts": [
                    {
                        "account_id": "H-GIA-J",
                        "type": "GIA",
                        "owner": "Joint",
                        "value": 40000.0,
                        "valuation_date": "2026-03-15",
                    }
                ],
            },
            "partner": {
                "name": "B",
                "accounts": [
                    {
                        "account_id": "H-GIA-J",
                        "type": "GIA",
                        "owner": "Joint",
                        "value": 40000.0,
                        "valuation_date": "2026-03-15",
                    },
                    {
                        "account_id": "H-ISA-S",
                        "type": "ISA",
                        "owner": "B",
                        "value": 58500.0,
                        "valuation_date": "2026-04-30",
                    },
                ],
            },
        },
    }
    accounts = parse_db_accounts(data)
    ids = [a.account_id for a in accounts]
    assert ids.count("H-GIA-J") == 1
    assert "H-ISA-S" in ids


def test_extract_db_preserves_null_values() -> None:
    data = {
        "snapshot_date": "2026-04-30",
        "holders": {
            "client": {
                "name": "Jean",
                "accounts": [
                    {
                        "account_id": "H-CASH-JE",
                        "type": "Cash Account",
                        "owner": "Jean",
                        "value": None,
                        "valuation_date": None,
                        "platform": "Holloway",
                        "status": "open",
                    }
                ],
            }
        },
    }
    obs = extract_db_observations(data, "client_data_db.json")
    values = [
        o for o in obs if o.field == "account_value" and o.account_id == "H-CASH-JE"
    ]
    assert len(values) == 1
    assert values[0].value is None
    assert values[0].source_role == "db"


def test_extract_db_from_real_client_01() -> None:
    path = Path(__file__).resolve().parents[1] / "data/client_01_clean/client_data_db.json"
    obs = extract_db_observations(path, path.name)
    value_ids = {o.account_id for o in obs if o.field == "account_value"}
    assert value_ids == {"H-ISA-01", "H-CASH-01"}
    isa = next(o for o in obs if o.field == "account_value" and o.account_id == "H-ISA-01")
    assert isa.value == 52000.0
    assert isa.as_of == "2026-04-30"


def test_extract_meeting_uses_llm_and_provenance() -> None:
    client = _mock_llm(
        {
            "meeting_date": "2026-05-14",
            "observations": [
                {
                    "field": "circumstances",
                    "value": "Both retired; unchanged",
                    "account_id": None,
                    "as_of": None,
                },
                {
                    "field": "account_value",
                    "value": 45000,
                    "account_id": "H-GIA-J",
                    "as_of": None,
                },
                {
                    "field": "recommendation_summary",
                    "value": "Disinvest joint GIA; top up both ISAs",
                    "account_id": None,
                    "as_of": None,
                },
            ],
        }
    )
    obs = extract_meeting_observations(
        "Meeting on 14 May 2026. GIA showed over 45000.",
        "meeting_notes.docx",
        openai_client=client,
        model="test",
    )
    by_field = {o.field: o for o in obs}
    assert by_field["circumstances"].source_role == "meeting"
    assert by_field["circumstances"].as_of == "2026-05-14"
    assert by_field["account_value"].value == 45000
    assert by_field["account_value"].account_id == "H-GIA-J"
    assert by_field["account_value"].as_of == "2026-05-14"


def test_extract_observations_composes_typed_sources(tmp_path: Path) -> None:
    db_path = tmp_path / "db.json"
    db_path.write_text(
        json.dumps(
            {
                "snapshot_date": "2026-04-30",
                "holders": {
                    "client": {
                        "name": "A",
                        "accounts": [
                            {
                                "account_id": "H-1",
                                "type": "ISA",
                                "owner": "A",
                                "value": 1000,
                                "valuation_date": "2026-04-30",
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    typed = {
        "request": {
            "name": "req.txt",
            "text": "Accounts covered | ISA\nSelling existing investments? | No\n",
            "path": tmp_path / "req.txt",
        },
        "db": {"name": "db.json", "text": db_path.read_text(), "path": db_path},
        "meeting": {"name": "meet.txt", "text": "We agreed to top up.", "path": tmp_path / "meet.txt"},
    }
    client = _mock_llm(
        {
            "meeting_date": "2026-05-01",
            "observations": [
                {
                    "field": "objectives",
                    "value": "Growth",
                    "account_id": None,
                    "as_of": None,
                }
            ],
        }
    )
    obs = extract_observations(typed, openai_client=client, model="test")
    roles = {o.source_role for o in obs}
    assert roles >= {"request", "db", "meeting"}
    assert any(o.field == "selling" and o.value is False for o in obs)
    assert any(o.field == "objectives" for o in obs)
