# doc-gen-assignment

Turns a client folder into an investment advice report. Runtime evidence is the report request, meeting notes, and custody data. `fde_notes.md` and `template_spec.md` are not ingested.

Requires Python 3.10+. Model is `OPENAI_MODEL` (default `gpt-4o-mini`).

## Setup

```bash
cp .env.example .env   # set OPENAI_API_KEY
uv sync                # or: pip install -e ".[dev]"
```

## Run

```bash
uv run python -m agent_pipeline.main --client client_01_clean
```

Writes `outputs/<client>.md` and `outputs/<client>/case_facts.json`.

| Flag | Default | |
|------|---------|---|
| `--client` | required | Folder name under `--data-dir` |
| `--data-dir` | `data` | |
| `--config` | `config/template_config.json` | |
| `--output-dir` | `outputs` | Ignored when `--run` is set |
| `--run LABEL` | | `outputs/runs/<timestamp>-<LABEL>/` |

Clients under `data/`: `client_01_clean`, `client_02_medium`, `client_03_hard`, `client_04_stretch`.

## Pipeline

`src/agent_pipeline/pipeline.py`: classify → extract → reconcile → write → footer.

1. **Classify** (`sources.py`). Images are noise. JSON with a top-level `holders` key is the custody file. Everything else is one batched model call. Unmatched files, and a failed call, become noise.
2. **Extract** (`extract.py`). Request labels and the custody file are parsed in code. The meeting note is a structured model call. Unknown account ids are dropped; only the custody file creates account rows.
3. **Reconcile** (`reconcile.py`). One `case_facts.json` per client: facts, provenance, recommendation actions, conflicts. Dated disagreements use the newest value and keep the other side. Values are not averaged. Money kind stays on the source fact (`account_balance`, `transfer_amount`, `received_proceeds`, `loan_repayment`, `contingent_proceeds`).
4. **Write** (`generate.py`, `render.py`). `config/template_config.json` defines sections. `source: render` slots (scope, holdings, fees, CGT) are code. `source: llm` slots get case facts, not the raw folder. Tax Implications is included only when `selling` is true.
5. **Footer** (`hitl.py`). Conflicts, `[REVIEW]` items from the body, and a source table.

`document_formatter/` only reads files and assembles the markdown.

One file is kept per role. A second meeting, request, or custody file overwrites the first.

## Eval

Deterministic checks, no model. Reported as five counts, not one score: **Read**, **Facts**, **Section**, **Story**, **Case**. Case checks the representation: only a numeric transfer becomes an action, a receipt or repayment does not, the same amount is not two flow kinds, every meeting £ amount is a fact value, and a disposal is a transfer on an account.

```bash
uv run python -m eval.checks --outputs-dir outputs
uv run python -m eval.checks --outputs-dir outputs/runs/<run> --record --note "what changed"
```

`--record` appends `outputs/eval/history.jsonl` and rewrites `outputs/eval/history.md`. Delta is per check against the previous score for that client. Exit code is 1 if any check fails or no `client_*.md` is found.

## Tests

```bash
uv run pytest
```

Offline. Live model calls are mocked.

| File | Covers |
|------|--------|
| `tests/test_sources.py` | Classification, noise, context selection |
| `tests/test_extract.py` | Request and custody parsing, meeting extract |
| `tests/test_reconcile.py` | Freshness, conflicts, money kinds, phantom ids |
| `tests/test_write_hitl.py` | Rendered slots, scope filter, tax gate, footer |
| `tests/test_eval_checks.py` | Check rules and history delta |

## Synthetic cases

`data/synthetic/generate.py` writes `syn_*` folders: same report themes, different people, figures, and file shapes. The pipeline does not read them unless `--data-dir` points there. `data/synthetic/FRAGILITIES.md` lists where the code is tied to the example clients.

## Other notes

- `DECISIONS.md` — stage choices and what was tried.
- `PROJECT_GUIDANCE.md` — original exercise brief.
- `README.old.md` — starter README.
