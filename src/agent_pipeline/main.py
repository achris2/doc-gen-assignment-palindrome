"""Application entry point.

Usage:
    python -m agent_pipeline.main --client client_01_clean
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent_pipeline.pipeline import generate_client_report
from agent_pipeline.schema import TemplateConfig


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
    config = TemplateConfig.from_dict(json.loads(config_text))
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
