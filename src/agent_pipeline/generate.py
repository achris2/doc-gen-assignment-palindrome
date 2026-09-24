"""Generate an advice report for a client from the template config.

Pipeline: Retrieve → Extract → Reconcile → Write (+ HITL footer).

Usage:
    python -m agent_pipeline.generate --client client_01_clean
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

from document_formatter.formatting import format_document

from agent_pipeline.extract import extract_observations
from agent_pipeline.hitl import append_hitl_footer
from agent_pipeline.reconcile import (
    build_case_document,
    reconcile_observations,
    write_facts_json,
)
from agent_pipeline.render import (
    facts_context_block,
    render_cgt_statement,
    render_fees,
    render_holdings_table,
    render_scope,
)
from agent_pipeline.sources import classify_client_files, load_typed_sources

# Placeholders filled from reconciled facts (no LLM).
# Prefer placeholder "source": "render" | "llm" in config; fall back to this set.
_DETERMINISTIC_SLOTS = {
    "scope": render_scope,
    "holdings_table": render_holdings_table,
    "fees": render_fees,
    "cgt_statement": render_cgt_statement,
}


class ReportGenerator:
    """Builds a report from reconciled facts + narrative LLM slots."""

    def __init__(self, openai_client: OpenAI, model: str) -> None:
        self._openai = openai_client
        self._model = model

    def generate(
        self,
        config: dict,
        *,
        facts: dict[str, Any],
        narrative_context: str,
    ) -> str:
        instructions = config.get("global_instructions", "")
        sections: list[dict[str, str]] = []
        for section in config["sections"]:
            if not self._section_applies(section, facts):
                continue
            sections.append(
                {
                    "title": section.get("title", ""),
                    "content": self._build_section(
                        section, facts, narrative_context, instructions
                    ),
                }
            )
        return format_document(config, sections)

    def _section_applies(self, section: dict, facts: dict[str, Any]) -> bool:
        rule = section.get("use_if", "always")
        if rule == "always":
            return True
        if isinstance(rule, dict):
            fact_name = rule.get("fact", "selling")
            equals = rule.get("equals", True)
            actual = facts.get(fact_name)
            return bool(actual) == bool(equals)
        # Legacy plain-language: Tax / selling gate
        if "sold" in str(rule).lower() or "selling" in str(rule).lower():
            return bool(facts.get("selling"))
        raise ValueError(f"Unknown use_if rule: {rule!r}")

    def _placeholder_is_render(self, name: str, spec: dict) -> bool:
        source = spec.get("source")
        if source == "render":
            return True
        if source == "llm":
            return False
        return name in _DETERMINISTIC_SLOTS

    def _build_section(
        self,
        section: dict,
        facts: dict[str, Any],
        narrative_context: str,
        instructions: str,
    ) -> str:
        content = section["template"]
        for name, spec in section.get("placeholders", {}).items():
            if self._placeholder_is_render(name, spec):
                renderer = _DETERMINISTIC_SLOTS.get(name)
                if renderer is None:
                    raise ValueError(f"No render function for placeholder {name!r}")
                value = renderer(facts)
            else:
                value = self._ask(
                    f"{instructions}\n\n{spec['prompt']}",
                    narrative_context,
                )
            content = content.replace(f"<<{name}>>", value)
        return content

    def _ask(self, instruction: str, context: str) -> str:
        response = self._openai.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "user", "content": f"{context}\n\n---\n\n{instruction}"}
            ],
        )
        return response.choices[0].message.content.strip()


def build_narrative_context(
    facts: dict[str, Any],
    typed_sources: dict[str, dict[str, Any]],
) -> str:
    """CASE FACTS + request/meeting text only (never noise/internal/full dump)."""
    parts = [facts_context_block(facts)]
    for role in ("request", "meeting"):
        src = typed_sources.get(role)
        if not src:
            continue
        name = src.get("name", role)
        text = src.get("text") or ""
        parts.append(f"=== {role}: {name} ===\n{text}")
    return "\n\n".join(parts)


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
    facts_path = write_facts_json(case, client_out / "case_facts.json")

    narrative = build_narrative_context(facts, typed)
    generator = ReportGenerator(openai_client, model)
    report = generator.generate(config, facts=facts, narrative_context=narrative)
    report = append_hitl_footer(report, facts, classifications)

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
