# doc-gen-assignment

Turns a client folder with different sources of information into a first-draft facts focused investment recommendation report with helpful information to a human reviewer.

Requires Python 3.10+. Model is `OPENAI_MODEL` (default `gpt-4o-mini`). Needs `OPENAI_API_KEY` to regenerate reports (not for pytest).

## Quickstart

```bash
cp .env.example .env          # set OPENAI_API_KEY
uv sync                       # install deps (or: pip install -e ".[dev]")

# Run the pipeline on data/client_01_clean → report + case_facts under outputs/
uv run python -m agent_pipeline.main --client client_01_clean
```

Default `--output-dir` is `outputs/`. `outputs/submission/` and `outputs/baseline/` stay untouched unless you pass them explicitly. Other clients: `client_02_medium`, `client_03_hard`, `client_04_stretch`. Use `--run` to keep experiments out of the way of top-level files:

```bash
# Same pipeline, dated experiment folder instead of top-level outputs/
uv run python -m agent_pipeline.main --client client_01_clean --run experiment
# → outputs/runs/<timestamp>-experiment/
```

| Flag | Default | |
|------|---------|---|
| `--client` | required | Folder name under `--data-dir` |
| `--data-dir` | `data` | |
| `--config` | `config/template_config.json` | |
| `--output-dir` | `outputs` | Ignored when `--run` is set |
| `--run LABEL` | | `outputs/runs/<timestamp>-<LABEL>/` |

Writes `outputs/<client>.md`, `outputs/<client>/case_facts.json`, and `outputs/<client>.usage.json`.

### Eval and tests (optional)

```bash
# Score finished reports in a directory. Deterministic, no model.
# Five buckets: read / facts / section / story / case → writes eval.md
uv run python -m eval.checks --outputs-dir outputs
uv run python -m eval.checks --outputs-dir outputs/submission   # frozen submission snapshot
uv run python -m eval.checks --outputs-dir outputs --record --note "what changed"
# --record also appends outputs/eval/history.jsonl (deltas vs previous run)

# Offline unit tests for each pipeline stage + the eval rules themselves.
# No API key; live model calls are mocked.
uv run pytest
#   test_sources      classify roles, noise, context selection
#   test_extract      request / custody parse, meeting extract
#   test_reconcile    freshness, conflicts, money kinds, phantom ids
#   test_write_hitl   render slots, tax gate, HITL footer
#   test_eval_checks  check rules and history delta
```

More detail: [Eval and tests](#eval-and-tests).

## Start here

### Architecture

```text
data/<client>/
  request · meeting · db          ← used as evidence
  noise / internal                ← dropped after classify
        │
        ▼
┌───────────────┐
│ 1. classify   │  sources.py     pattern + LLM → FileRole
└───────┬───────┘
        ▼
┌───────────────┐
│ 2. extract    │  extract.py     → Observation (+ MeetingDecision)
└───────┬───────┘                 code: request / db · LLM: meeting
        ▼
┌───────────────┐
│ 3. reconcile  │  reconcile.py   → CaseDocument
└───────┬───────┘                 case_facts.json
        ▼
┌───────────────┐
│ 4. write      │  generate +     template_config.json
│               │  render         code slots · LLM slots
└───────┬───────┘
        ▼
┌───────────────┐
│ 5. footer     │  hitl.py        conflicts · [REVIEW] · sources
└───────┬───────┘
        ▼
outputs/<client>.md
outputs/<client>/case_facts.json
        │
        └── eval.checks → scorecard (read / facts / section / story / case)
```

Code owns identity, money kinds, section gates, and eval. The model classifies ambiguous files, extracts the meeting, and writes limited prose. Details: `DECISIONS.md`.

| Path | Kind |
|------|------|
| `outputs/<client>.md` + `case_facts.json` | live (default) |
| `outputs/runs/<stamp>-<label>/` | live (`--run`) |
| `outputs/submission/`, `outputs/baseline/` | static snapshots |
| `outputs/eval/history.jsonl` | from `--record` |

### Outputs

Live runs write under `outputs/` (or `outputs/runs/<timestamp>-<label>/` with `--run`). Two **static** snapshots are checked in for review — the pipeline does not write to them unless you point `--output-dir` there:

| Folder | What it is |
|--------|------------|
| `outputs/baseline/` | Frozen reports from the **original** pipeline (raw folder → section prompts), to be used as a baseline. Markdown only — no case file. |
| `outputs/submission/` | Frozen reports from the **final** pipeline. Markdown plus reconciled case per client, plus `eval.md`. |

```
outputs/                          # default --output-dir (live regenerations)
  client_01_clean.md
  client_01_clean/case_facts.json
  …
outputs/submission/               # static snapshot for review / grading
  client_01_clean.md
  client_01_clean/case_facts.json
  …
  eval.md
  notes.md
outputs/baseline/                 # static original-pipeline reports
  client_01_clean.md
  …
```

Clients: `client_01_clean`, `client_02_medium`, `client_03_hard`, `client_04_stretch`. Compare baseline vs submission side by side; use `case_facts.json` for what the final system decided before prose. `eval.md` is a five-bucket scorecard (see [Eval and tests](#eval-and-tests)) it is focused on helping with regression protection as the pipeline gets iterated on, rather than a proof the report is perfect.

### Docs worth reading

| Doc | Why |
|-----|-----|
| `PROJECT_GUIDANCE.md` | Original exercise brief. |
| `DECISIONS.md` | Hardest design calls and what was tried. |
| `config/template_config.json` | Report shape: sections, `use_if`, render vs llm slots, prompts. |
| `src/agent_pipeline/schema.py` | Stage contracts (dataclasses between stages). |
| `README_OLD.md` | Starter README from the original. |

## Entry points

| Concern | Where |
|---------|--------|
| CLI | `src/agent_pipeline/main.py` → `generate_client_report` |
| Orchestration | `src/agent_pipeline/pipeline.py` |
| Classify | `src/agent_pipeline/sources.py` |
| Extract | `src/agent_pipeline/extract.py` |
| Reconcile | `src/agent_pipeline/reconcile.py` |
| Write / render | `src/agent_pipeline/generate.py`, `render.py` |
| HITL footer | `src/agent_pipeline/hitl.py` |
| Contracts | `src/agent_pipeline/schema.py` |
| Report config | `config/template_config.json` |
| Eval | `src/eval/checks.py` |
| File IO / markdown assemble | `src/document_formatter/` (scaffold plumbing) |

```
data/                 four clients (+ synthetic/)
config/               template_config.json
src/agent_pipeline/   pipeline
src/eval/             deterministic checks
src/document_formatter/
outputs/              live runs; plus static baseline/ and submission/
tests/
```

## How it works

`pipeline.py`: classify → extract → reconcile → write → footer.

1. **Classify** (`sources.py`). Images are noise. JSON with a top-level `holders` key is the custody file. Everything else is one batched model call; a failed call (or no model) marks those files as noise.
2. **Extract** (`extract.py`). Request labels and the custody file are parsed in code. The meeting note is a structured model call. Unknown account ids are dropped; only the custody file creates account rows.
3. **Reconcile** (`reconcile.py`). One `case_facts.json` per client: facts, provenance, recommendation actions, conflicts. Dated disagreements use the newest value and keep the other side. Values are not averaged. Money kind stays on the source fact.
4. **Write** (`generate.py`, `render.py`). `template_config.json` defines sections. `source: render` slots (scope, holdings, fees, CGT) are code. `source: llm` slots get case facts, not the raw folder. Tax Implications only when `selling` is true.
5. **Footer** (`hitl.py`). Conflicts, `[REVIEW]` items from the body, and a source table.

One file is kept per role. A second meeting, request, or custody file overwrites the first.

Diagrams: [Architecture](#architecture). Judgement and rejected alternatives: `DECISIONS.md`.

## Data model

Contracts live in `schema.py`:

```
TypedSource / FileRole
  → Observation (+ MeetingDecision)
  → ReconciledFacts / Conflict / Account
  → CaseDocument  (case_facts.json)
  → TemplateConfig placeholders → markdown + HITL footer
```

| Type | Role |
|------|------|
| `FileRole` / `TypedSource` | What each client file is; only `request` / `meeting` / `db` load. |
| `Observation` | One extracted field + provenance (optional `MoneyKind`). |
| `MeetingDecision` | Dispose / retain / confirm / contribute intent (separate from money). |
| `ReconciledFacts`, `Conflict`, `Account` | Freshness pick, open disagreements, known accounts only. |
| `CaseFact`, `RecommendationAction`, `Decision` | Facts vs numeric-transfer actions vs evidence-backed decisions — not interchangeable. |
| `CaseDocument` | Persistable case: `run`, `sources`, `facts`, `actions`, `decisions`, `sections`, `selling`, `conflicts`, `review_items`, `accounts`. |
| `TemplateConfig` / `PlaceholderSpec` | Each `<<slot>>` is `source: render` or `source: llm`. |

`MoneyKind`: `account_balance`, `transfer_amount`, `received_proceeds`, `loan_repayment`, `contingent_proceeds`. Only a numeric `transfer_amount` becomes a recommendation action.

## Eval and tests

### Eval

Deterministic checks, no model. Five counts, not one score:

| Bucket | What it asks |
|--------|----------------|
| **Read** | Did the case record the sources it used? |
| **Facts** | Is the case shape valid? |
| **Section** | FCA line, risk warning, no £ in background summary, `[REVIEW]` in footer, action items. |
| **Story** | Conflicts surfaced; tax iff `selling`; holdings ids resolve. |
| **Case** | Only a numeric transfer is an action; receipts/repayments are not; same amount ≠ two flow kinds; meeting £ amounts are facts; `selling` needs a supported disposal decision. |

```bash
# Score whatever directory holds the reports (live outputs/, a --run dir, or the static snapshot)
uv run python -m eval.checks --outputs-dir outputs
uv run python -m eval.checks --outputs-dir outputs/runs/<run>
uv run python -m eval.checks --outputs-dir outputs/submission

uv run python -m eval.checks --outputs-dir outputs --record --note "what changed"
```

`--record` appends `outputs/eval/history.jsonl` and rewrites `outputs/eval/history.md`. Exit code is 1 if any check fails or no `client_*.md` is found. Eval reads each report plus `case_facts.json` when present (and `--data-dir` for source checks). Baseline cannot pass the full suite — it has no case file.

### Tests

```bash
uv run pytest
```

Offline contract for deterministic rules (classification fail-closed, phantom accounts, money kinds, tax gate, footer, eval itself). No API key; live calls are mocked.

| File | Covers |
|------|--------|
| `tests/test_sources.py` | Classification, noise, context selection |
| `tests/test_extract.py` | Request and custody parsing, meeting extract |
| `tests/test_reconcile.py` | Freshness, conflicts, money kinds, phantom ids |
| `tests/test_write_hitl.py` | Rendered slots, scope filter, tax gate, footer |
| `tests/test_eval_checks.py` | Check rules and history delta |

## Limits and held-out

- One core file per role; multiple meetings / requests / custody snapshots are not fully reconciled.
- Images, PDFs, nested files, and other multimodal sources are skipped — material facts there are missed by design for this exercise.
- Request / custody parsing assumes recognisable shapes before falling back to the model.
- Deterministic evals cover known invariants, not tone or suitability for sign-off.

`data/synthetic/` (`uv run python data/synthetic/generate.py`) holds held-out-style folders with the same themes and different specifics. The pipeline does not read them unless `--data-dir` points there. Prompts were not fitted to these folders.

What I would take further: `DECISIONS.md` → With more time.
