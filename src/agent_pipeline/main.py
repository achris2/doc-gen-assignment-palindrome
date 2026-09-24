"""Application entry point.

Retrieve → Extract → Reconcile → Write (+ HITL footer).

Usage:
    python -m agent_pipeline.main --client client_01_clean
"""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from agent_pipeline.extract import extract_observations
from agent_pipeline.generate import ReportGenerator
from agent_pipeline.hitl import append_hitl_footer
from agent_pipeline.reconcile import (
    build_case_document,
    reconcile_observations,
    write_facts_json,
)
from agent_pipeline.sources import classify_client_files, load_typed_sources


def run_header(label: str, config_text: str, model: str) -> dict[str, Any]:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    return {
        "label": label,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "config_sha": hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
        "git_rev": revision or "unknown",
    }


def generate_client_report(
    client_dir: Path,
    config: dict,
    *,
    openai_client: OpenAI,
    model: str,
    output_dir: Path,
    client_name: str,
    run_label: str = "",
    config_text: str = "",
) -> tuple[Path, Path]:
    """Full Retrieve → Extract → Reconcile → Write pipeline for one client."""
    classifications = classify_client_files(
        client_dir, openai_client=openai_client, model=model
    )
    typed = load_typed_sources(
        client_dir,
        openai_client=openai_client,
        model=model,
        classifications=classifications,
    )
    observations = extract_observations(
        typed, openai_client=openai_client, model=model
    )
    facts = reconcile_observations(observations)
    case = build_case_document(
        facts,
        classifications,
        run=run_header(run_label or client_name, config_text, model),
    )

    client_out = output_dir / client_name
    generator = ReportGenerator(openai_client, model)
    report = generator.generate(config, facts=facts, case=case)
    report = append_hitl_footer(report, facts, classifications)

    facts_path = write_facts_json(case, client_out / "case_facts.json")
    out_path = output_dir / f"{client_name}.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return out_path, facts_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an advice report for a client."
    )
    parser.add_argument("--client", required=True, help="folder name under data/")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--config", type=Path, default=Path("config/template_config.json")
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--run",
        default=None,
        help="Write outputs/runs/<timestamp>-<label>/ instead of outputs/",
    )
    args = parser.parse_args()

    load_dotenv()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI()
    config_text = args.config.read_text(encoding="utf-8")
    config = json.loads(config_text)
    client_dir = args.data_dir / args.client
    if args.run:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        output_dir = Path("outputs") / "runs" / f"{stamp}-{args.run}"
    else:
        output_dir = args.output_dir or Path("outputs")

    out_path, facts_path = generate_client_report(
        client_dir,
        config,
        openai_client=client,
        model=model,
        output_dir=output_dir,
        client_name=args.client,
        run_label=args.run or args.client,
        config_text=config_text,
    )
    print(f"Wrote {out_path}")
    print(f"Wrote {facts_path}")


if __name__ == "__main__":
    main()
