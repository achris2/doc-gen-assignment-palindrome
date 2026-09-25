# doc-gen-assignment

A small config-driven pipeline that turns a client's source material into an advice report. Start
with `PROJECT_GUIDANCE.md`; it describes the exercise.

## Setup

```bash
# clone your fork, then:
cp .env.example .env          # put your OpenAI key in .env
uv sync                       # or: pip install -e .
```

## Run

```bash
uv run python -m agent_pipeline.main --client client_01_clean
# report is written to outputs/client_01_clean.md - This will run for client_01, we would recommend reviewing data/code before running on other clients.
```

Available clients live under `data/`:

- `client_01_clean`
- `client_02_medium`
- `client_03_hard`
- `client_04_stretch` (large and messy on purpose; a deliberate stretch)

## How it fits together

```
config/template_config.json          the report definition: sections, prompts, inclusion rules
        │
        ▼
src/agent_pipeline/main.py           entry point: Retrieve → Extract → Reconcile → Write
        │
        ▼
src/agent_pipeline/generate.py       for each section, decides inclusion and fills prompts
        ▼
src/document_formatter/formatting.py assembles the sections into the final .md document
        │
        ▼
outputs/<client>.md
```

## The pipeline is deliberately minimal

What you are given is the smallest setup that runs end to end. It works, but it is naive: one model
call per section, every file in the client folder dumped into every prompt whether or not it is
relevant, a single model, and no tools. Improving it is part of the task; weigh speed, cost, and
effectiveness (see `PROJECT_GUIDANCE.md`). Most of your work will be in
`config/template_config.json`; the pipeline code is yours to improve too.
