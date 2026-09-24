# Decisions

The starter dumped every file in the client folder into every section prompt. That is cheap to run and wrong for a suitability report: noise files dilute the case, the model invents fees and CGT, and a disagreement between the meeting and the custody snapshot has nowhere to go except the prose.

The pipeline is now classify → extract → reconcile → write → footer. One model (`gpt-4o-mini`) plus deterministic rules. Runtime evidence is the request, the meeting note, and the custody file. `fde_notes.md` and `template_spec.md` are author-time only: the hierarchy they describe is encoded in code, and they are not ingested into `case_facts.json`.

## What changed

**Prompt-only fixes, still on the starter.** Global instructions, a static FCA line and risk warning, `[REVIEW]` instead of an estimated CGT or a guessed fee, tighter scope / background / recommendation prompts, and a constrained holdings table. These stopped the worst inventions. They did not stop the model re-reading the raw folder, so the next step was to take the folder out of the narrative prompts.

**Classify, then extract.** Images are noise. JSON with a top-level `holders` key is the custody file. Everything else is one batched classification call; a failed call is noise. The request and the custody file are parsed in code. The meeting note is one structured call. Unknown account ids are dropped. Only the custody file creates account rows, so a meeting phrase cannot invent `joint_GIA` or `Holloway cash account`.

**Reconcile in code.** Request fields come from the request, meeting narrative from the meeting, account identity from the database. A dated disagreement on a balance uses the newest value and keeps the other side. Values are not averaged. An undated disagreement stays open. Closed accounts leave the holdings table. Conflicts are shown in the table and again in the human-review footer, with file and quote. Body `[REVIEW]` tags are merged into that footer.

**Write from the case, not the folder.** `config/template_config.json` still defines the document. `source: render` slots (scope, holdings, fees, CGT) are code. `source: llm` slots receive case facts and must cite fact ids. Tax Implications is included only when `selling` is true. Section gates are config (`always` or a fact equals a value), not a sentence the model is asked to interpret.

**One case file per run.** `outputs/<client>/case_facts.json` holds sources, typed facts, recommendation actions, and the items each narrative slot returned. `--run <label>` writes `outputs/runs/<timestamp>-<label>/` and records model, config hash, and git revision, so a prompt change is a different run.

**Money is typed, and a recommendation is one action.** Kind stays on the source fact: `account_balance`, `transfer_amount`, `received_proceeds`, `loan_repayment`, `contingent_proceeds`. A meeting line about moving £20,000 is a transfer, not a new balance. Each non-balance money fact becomes one action with `amount` and `supports` (the fact id). The action has no kind. The recommendation slot returns one item per action id, and the amount check compares that sentence only with that action. A request `investment_amount` that equals exactly one transfer is corroboration on that action, not a second recommendation. The stored action summary is what the slot is given to phrase.

**Eval is four counts, not one score.** Read (were the core files parsed and images skipped), Facts (id, source, excerpt, kind, conflict evidence), Section (FCA line, risk warning, one item per action, no £ in the background summary, body review tags in the footer), Story (tax section iff selling, table ids, conflicts surfaced). Counts are not averaged. Coherence stays a human note on the run. Early deterministic checks on the regenerated reports went from 22/28 (phantom rows, missing footer review, a pound figure in a background summary) to 28/28; the four-way scorecard replaced that single total. Recent recorded runs are 1/1, 1/1, 5/5, 3/3. A background-prompt change that still scored full marks was discarded after a human read. A Client 4 run was discarded when fact ids moved, because it could no longer test the recommendation wording.

**Types after the behaviour settled.** Observations, accounts, the case file, the template, and model replies were dicts passed between stages. They are types in `schema.py`. Closed vocabularies (roles, fact fields, money kinds) are literals shared by classify, extract, and reconcile. `document_formatter/` still only reads files and assembles markdown. `case_facts.json` shape is unchanged.

## Hardest calls

- **Freshness plus a flag, never an average.** The draft uses the newest dated value. The other side stays visible for the adviser. Averaging two snapshots would hide the disagreement the report exists to surface.
- **The model does not own identity, money, or fixed wording.** It classifies leftover files, extracts the meeting, and writes narrative. Code owns account ids, the holdings table, the FCA line, the risk warning, fee and CGT gaps, and which sections appear.
- **Colleague notes are not evidence.** `fde_notes.md` is an incomplete earlier pass. Treating it as a source would let a scratch figure into the case.
- **Kind belongs to the fact, not the action.** Two actions in one recommendation let a checker guess which sentence used which figure. One item per action id removes that guess. Collapsing a request amount onto a single matching transfer stops the same £20,000 being recommended twice.
- **Do not average the eval.** A report can read the files and still tell the wrong story. Separate counts show which of those failed. An assertion suite cannot judge tone; that stays a note beside the run.

## Limits

Checks are assertions, not an LLM judge. They do not contain client names or figures.

A meeting £20,000 on `H-CASH-01` can still be typed as a balance when the note meant a transfer. Flagging that as a value conflict is the conservative result. The extract label is what is imperfect.

One file is kept per role. A second meeting, request, or custody file overwrites the first. Request labels are an allowlist of exact strings. Scope is a bag of type words (`isa`, `gia`, `sipp`, `pension`, `bond`, `cash`, `joint`). Status must be the string `closed`. Dates must be ISO. Amounts print as sterling. Subfolders are not opened. `data/synthetic/` and `FRAGILITIES.md` list these ties. The pipeline does not read that folder unless `--data-dir` points there.

## With more time

- Read every file of a role, and keep a second meeting when the first was rejected.
- Parse a request that is prose, or whose labels are not the eight known strings, instead of dropping the row.
- Accept a custody file that is not a `holders` object, and more than one row for the same account id.
- Parse non-ISO dates so a later figure is not treated as undated.
- Scope by account id as well as type word. Treat dormant, frozen, and pending closure like closed when that is the intent.
- Keep currency on the figure. Do not print a USD bond as pounds.
- Open nested files. Leave images unread until there is a vision step, and say so.
- Stop a reconstruction step from citing an account id or a figure that is not already on the evidence.
- A stronger model as a compliance gate after the draft, still forbidden to invent a fee or a CGT figure.
- Pairwise review of narrative, in CI, beside the assertion counts. Not instead of them.
