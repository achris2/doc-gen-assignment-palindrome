"""Tests for content-based source role classification (deterministic + LLM mock)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_pipeline import sources
from agent_pipeline.sources import (
    classify_by_pattern,
    classify_client_files,
    load_typed_sources,
    select_context_files,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _mock_client(classifications: list[dict[str, str]]) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps({"classifications": classifications})
                )
            )
        ]
    )
    return client


def test_db_json_with_holders(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps({"holders": {}, "snapshot_date": "2026-01-01"}), encoding="utf-8"
    )
    assert classify_by_pattern(path) == "db"


def test_image_is_noise(tmp_path: Path) -> None:
    path = tmp_path / "statement.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert classify_by_pattern(path) == "noise"


def test_text_files_not_classified_deterministically(tmp_path: Path) -> None:
    request = _write(
        tmp_path / "instruction.txt",
        "Report Requirement Summary\n"
        "Accounts covered | Holloway ISA\n"
        "Selling existing investments? | No\n"
        "Product recommended | Top up\n",
    )
    meeting = _write(
        tmp_path / "notes.txt",
        "Annual review meeting with the client.\n"
        "We agreed circumstances and objectives are unchanged.\n",
    )
    noise = _write(
        tmp_path / "market.txt",
        "This update does not relate to any individual client.\n"
        "General market commentary for the quarter.\n",
    )
    internal = _write(
        tmp_path / "spec.md", "# Report specification: Investment Advice Report\n"
    )
    assert classify_by_pattern(request) is None
    assert classify_by_pattern(meeting) is None
    assert classify_by_pattern(noise) is None
    assert classify_by_pattern(internal) is None


def test_unmatched_without_llm_is_noise(tmp_path: Path) -> None:
    _write(tmp_path / "random.txt", "hello world nothing useful here")
    roles = classify_client_files(tmp_path)
    assert roles["random.txt"] == "noise"


def test_llm_classifies_text_roles(tmp_path: Path) -> None:
    _write(tmp_path / "req.txt", "Report Requirement Summary\nSelling existing investments? | No\n")
    _write(tmp_path / "meet.txt", "Review meeting. We agreed objectives remain.\n")
    _write(tmp_path / "noise.txt", "General market commentary for the quarter.\n")
    _write(tmp_path / "spec.md", "# Report specification\n")
    (tmp_path / "db.json").write_text(json.dumps({"holders": {}}), encoding="utf-8")

    client = _mock_client(
        [
            {"name": "req.txt", "role": "request"},
            {"name": "meet.txt", "role": "meeting"},
            {"name": "noise.txt", "role": "noise"},
            {"name": "spec.md", "role": "internal"},
        ]
    )
    roles = classify_client_files(tmp_path, openai_client=client, model="test-model")
    assert roles["db.json"] == "db"
    assert roles["req.txt"] == "request"
    assert roles["meet.txt"] == "meeting"
    assert roles["noise.txt"] == "noise"
    assert roles["spec.md"] == "internal"
    assert client.chat.completions.create.called


def test_llm_failure_defaults_unmatched_to_noise(tmp_path: Path) -> None:
    _write(tmp_path / "req.txt", "Accounts covered | ISA\n")
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("api down")
    roles = classify_client_files(tmp_path, openai_client=client, model="test-model")
    assert roles["req.txt"] == "noise"


def test_select_context_excludes_noise_and_internal(tmp_path: Path) -> None:
    (tmp_path / "db.json").write_text(
        json.dumps({"holders": {"client": {"name": "A", "accounts": []}}}),
        encoding="utf-8",
    )
    _write(tmp_path / "req.txt", "Accounts covered | ISA\nSelling existing investments? | No\n")
    _write(tmp_path / "meet.txt", "Review meeting held today.\n")
    _write(tmp_path / "noise.txt", "General market commentary.\n")
    _write(tmp_path / "spec.md", "# Report specification\n")

    client = _mock_client(
        [
            {"name": "req.txt", "role": "request"},
            {"name": "meet.txt", "role": "meeting"},
            {"name": "noise.txt", "role": "noise"},
            {"name": "spec.md", "role": "internal"},
        ]
    )
    selected = select_context_files(tmp_path, openai_client=client, model="test-model")
    assert set(selected) == {"db.json", "req.txt", "meet.txt"}


def test_renamed_cores_still_classify_with_llm(tmp_path: Path) -> None:
    (tmp_path / "accounts_export.json").write_text(
        json.dumps({"holders": {}, "snapshot_date": "2026-04-30"}),
        encoding="utf-8",
    )
    _write(tmp_path / "adviser_brief.docx.txt", "Selling existing investments? | Yes\n")
    _write(tmp_path / "file_note_2026.txt", "Planning meeting with the clients.\n")

    client = _mock_client(
        [
            {"name": "adviser_brief.docx.txt", "role": "request"},
            {"name": "file_note_2026.txt", "role": "meeting"},
        ]
    )
    roles = classify_client_files(tmp_path, openai_client=client, model="test-model")
    assert roles["accounts_export.json"] == "db"
    assert roles["adviser_brief.docx.txt"] == "request"
    assert roles["file_note_2026.txt"] == "meeting"


@pytest.mark.parametrize(
    "client",
    ["client_01_clean", "client_02_medium", "client_03_hard", "client_04_stretch"],
)
def test_smoke_real_clients_deterministic_db_and_unmatched_text(client: str) -> None:
    """Offline smoke: db via holders; text roles require LLM (default to noise)."""
    root = Path(__file__).resolve().parents[1]
    client_dir = root / "data" / client
    if not client_dir.is_dir():
        pytest.skip(f"missing {client_dir}")

    roles = classify_client_files(client_dir)  # no OpenAI → text → noise
    assert "db" in roles.values()
    db_name = next(n for n, r in roles.items() if r == "db")
    assert db_name.endswith(".json")

    # Without LLM, non-db non-image files must not be treated as core by accident
    for name, role in roles.items():
        path = client_dir / name
        if path.suffix.lower() in sources.IMAGE_SUFFIXES:
            assert role == "noise"
        elif role == "db":
            continue
        else:
            assert role == "noise"

    # With a stub LLM that labels known filenames, cores appear and noise stays out
    text_files = [
        p.name
        for p in client_dir.iterdir()
        if p.is_file() and classify_by_pattern(p) is None
    ]
    stub_map: dict[str, str] = {}
    for name in text_files:
        lower = name.lower()
        if "request" in lower or "report_request" in lower:
            stub_map[name] = "request"
        elif "meeting" in lower:
            stub_map[name] = "meeting"
        elif "fde_notes" in lower or "template_spec" in lower:
            stub_map[name] = "internal"
        else:
            stub_map[name] = "noise"

    mock = _mock_client([{"name": n, "role": r} for n, r in stub_map.items()])
    roles_llm = classify_client_files(client_dir, openai_client=mock, model="test-model")
    assert "request" in roles_llm.values()
    assert "meeting" in roles_llm.values()
    assert "db" in roles_llm.values()
    selected = select_context_files(client_dir, openai_client=mock, model="test-model")
    for name in selected:
        assert roles_llm[name] in {"request", "meeting", "db"}
    typed = load_typed_sources(client_dir, openai_client=mock, model="test-model")
    assert set(typed) >= {"request", "meeting", "db"}
