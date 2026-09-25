"""Retrieve → Extract → Reconcile → Write for one client."""

import hashlib
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from agent_pipeline.extract import decisions_for_meeting, extract_observations
from agent_pipeline.generate import ReportGenerator
from agent_pipeline.hitl import append_hitl_footer
from agent_pipeline.llm import collect_usage, stop_usage
from agent_pipeline.reconcile import build_case_document, reconcile_observations, write_facts_json
from agent_pipeline.schema import MeetingDecision, RunHeader, TemplateConfig
from agent_pipeline.sources import classify_client_files, load_typed_sources


def run_header(label: str, config_text: str, model: str) -> RunHeader:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    return RunHeader(
        label=label,
        recorded_at=datetime.now().isoformat(timespec="seconds"),
        model=model,
        config_sha=hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
        git_rev=revision or "unknown",
    )


def _write_usage(path: Path, calls: list[dict], pipeline_ms: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"pipeline_ms": round(pipeline_ms, 1), "calls": calls},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        return


def generate_client_report(
    client_dir: Path,
    config: TemplateConfig,
    *,
    openai_client: OpenAI,
    model: str,
    output_dir: Path,
    client_name: str,
    run_label: str = "",
    config_text: str = "",
) -> tuple[Path, Path]:
    """Full Retrieve → Extract → Reconcile → Write pipeline for one client."""
    calls, token = collect_usage()
    started = time.perf_counter()
    try:
        return _generate_client_report(
            client_dir,
            config,
            openai_client=openai_client,
            model=model,
            output_dir=output_dir,
            client_name=client_name,
            run_label=run_label,
            config_text=config_text,
        )
    finally:
        stop_usage(token)
        _write_usage(
            output_dir / f"{client_name}.usage.json",
            calls,
            (time.perf_counter() - started) * 1000,
        )


def _generate_client_report(
    client_dir: Path,
    config: TemplateConfig,
    *,
    openai_client: OpenAI,
    model: str,
    output_dir: Path,
    client_name: str,
    run_label: str = "",
    config_text: str = "",
) -> tuple[Path, Path]:
    classifications = classify_client_files(
        client_dir, openai_client=openai_client, model=model
    )
    typed = load_typed_sources(
        client_dir,
        openai_client=openai_client,
        model=model,
        classifications=classifications,
    )
    observations = extract_observations(typed, openai_client=openai_client, model=model)
    meeting_decisions: list[MeetingDecision] = []
    meeting = typed.get("meeting")
    if meeting is not None:
        meeting_decisions = decisions_for_meeting(
            meeting,
            observations,
            openai_client=openai_client,
            model=model,
        )
    facts = reconcile_observations(observations)
    case = build_case_document(
        facts,
        classifications,
        run=run_header(run_label or client_name, config_text, model),
        meeting_decisions=meeting_decisions,
    )

    client_out = output_dir / client_name
    report = ReportGenerator(openai_client, model).generate(config, facts=facts, case=case)
    report = append_hitl_footer(report, facts, classifications)

    facts_path = write_facts_json(case, client_out / "case_facts.json")
    out_path = output_dir / f"{client_name}.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return out_path, facts_path
