# Decisions

The starter put every file in the client folder into every section prompt. That mixed client evidence with notes, images and noise, made the model repeatedly reinterpret the same case, and gave conflicting sources nowhere to go except the prose.

Final shape:

**classify → extract → reconcile → write → human review**

One model (`gpt-4o-mini`) plus deterministic rules. Generation writes from a reconciled case, not the raw folder.

## Hardest calls

* **Assume human review, so surface conflicts rather than manufacture certainty.**

  * I treated this as a draft suitability-report workflow where an adviser/paraplanner reviews the output before use.
  * Given that assumption, a visible disagreement is preferable to a confident but unsupported answer.
  * If the meeting says £255k and the database says £240k, for example: 
  * Dated snapshots: use the newer value and retain the other side for review.
  * Undated / genuinely unresolved disagreement: leave the conflict open.
  * The purpose of Human Review is not to make every warning disappear; it is to put the ambiguity in front of the person able to resolve it.

* **No single golden source.**

  * Source authority is field-specific:

    * request → what the report should cover;
    * custody / DB → account identity and system-held records;
    * meeting → recent circumstances, objectives and intent.
  * Account values are reconciled separately using dates and evidence.
  * “Always trust the database” or “always trust the latest meeting” would both be wrong for some fields.

* **The model does not own identity, money or fixed wording.**

  * Code owns account ids, holdings rows, section gates, known values, fee / CGT gaps and fixed wording.
  * The model handles ambiguous classification, meeting extraction and limited narrative.
  * In this context, inventing an account, balance, fee, CGT figure or recommendation is a worse failure than flat prose.

* **Money keeps its meaning; facts, actions and decisions stay separate.**

  * The same £ figure can be a balance, transfer, receipt, repayment or contingent proceeds.
  * Treating those as interchangeable caused some of the hardest regressions.
  * Receipts / repayments / contingent proceeds remain facts.
  * A supported numeric transfer can become an action.
  * Dispose / retain / confirm / contribute are decisions and need their own evidence.
  * Matching evidence corroborates an action; it should not create a second recommendation.
  * If a recommendation is agreed but the amount is not, the amount stays unfixed rather than being invented.

* **`selling` gates Tax Implications; it does not create a disposal.**

  * An early eval linked selling to a transfer.
  * The harder case showed that the relevant concept was a supported disposal decision, not necessarily a transfer.
  * I changed the invariant rather than changing the pipeline to satisfy a bad test.
  * The eval is code too; it can encode the wrong model of the problem.

* **`fde_notes.md` and `template_spec.md` are not client evidence.**

  * `fde_notes.md` is explicitly incomplete and should not be able to introduce a client fact.
  * `template_spec.md` defines the report, not the client's circumstances.
  * Useful rules are encoded in code/config rather than supplied as runtime evidence.
  * other files proved to not necessarily be relevant and were excluded, which may make it less general / robust to different data 

* **Deliberately exclude unsupported / multimodal sources for this exercise.**

  * Images, platform statement screenshots, nested files and other unsupported formats are not treated as evidence.
  * This makes the current pipeline more fragile: a material fact could exist only in one of those files.
  * It was a deliberate scope choice. Without a clear production source contract, trying to support arbitrary files would add a lot of complexity and put more judgement back into the model.
  * In production I would expect multimodal / document ingestion with explicit provenance, not silently ignoring those sources.

* **Eval is several checks, not one score.**

  * `read / facts / section / story / case` remain separate because a report can parse all the files correctly and still tell the wrong story.
  * Checks target general invariants, not the four supplied clients.
  * Examples: ids must resolve to evidence; conflicts keep both sides; receipts are not actions; recommendation amounts are supported; missing fees / CGT are not invented.
  * At least one prompt change passed the deterministic checks but made the output worse on human inspection.
  * The suite is regression protection, not proof that the narrative is good.

## Approach that followed from those calls

* **Kept `gpt-4o-mini`: cheap model, less model authority.**

  * With limited API credits and repeated runs across four clients, cheap iteration mattered. Although newer, more powerful models could also help, that was a deliberate choice for this scope. 
  * The accepted cost was weaker prose and weaker handling of ambiguous text.
  * Rather than keep increasing context, I reduced the number of things the model was responsible for deciding.

* **Prompt tuning first, architecture second.**

  * I started with the simplest intervention: tighter prompts in `template_config.json`.
  * That reduced obvious inventions around CGT, fees, recommendations and holdings.
  * It did not fix the structural problem that every section was re-reading the same noisy folder.
  * That led to the case representation and the classify → extract → reconcile → write flow.

* **Extract once, reconcile once, write from the case.**

  * Structured request / custody data is parsed in code where possible.
  * Meeting notes are extracted into observations with provenance.
  * Report generation receives reconciled case facts rather than raw documents.
  * This reduces context, repeated interpretation and inconsistencies between sections.

* **Only structured account evidence creates account rows.**

  * Meeting text can add information about a known account but should not invent a new account identity.
  * This directly addressed phantom rows from early runs.

* **Make section inclusion deterministic where possible.**

  * Tax Implications follows structured case state rather than asking the model whether tax “sounds relevant”.
  * Section presence is easy to test and does not benefit from model variability.

* **Types after behaviour settled.**

  * Early dict-based structures made it faster to change the pipeline while I was still understanding the problem.
  * Once the stage boundaries stabilised, dataclasses / literals made the contracts between stages clearer.
  * I deliberately kept external ingestion looser; fitting a rigid schema to the four supplied clients would risk overfitting the held-out set.

* **Keep runs reproducible.**

  * Persist the reconciled case plus model / config / git metadata.
  * Prompt and config changes then produce comparable runs rather than orphan markdown outputs.

## Limits

* One core file is retained for some roles; multiple meetings / requests / custody snapshots need proper chronology and reconciliation.
* Unsupported images, PDFs / scans, nested files and other multimodal sources can contain evidence the current pipeline misses.
* Request / custody parsing still assumes recognisable shapes before falling back to the model.
* Date, account-status and currency handling are narrower than I would want in production.
* Meeting money can still be mistyped; the conservative failure mode is to surface a conflict rather than silently choose a figure.
* Deterministic evals cover known invariants, not global correctness, tone or suitability for sign-off.

## With more time

* **Push prompt tuning systematically.**

  * Use the synthetic cases plus a much larger dataset to see how far narrative quality can be improved while treating factual-grounding checks as hard regressions.
  * Version prompts, run them against the same cases and keep changes based on evidence rather than individual outputs.

* **Add human-labelled ground truth.**

  * Have advisers / paraplanners label facts, conflicts, actions, decisions and review items.
  * This would let extraction and reconciliation be evaluated directly instead of judging everything through the final report.

* **Add an LLM-judge layer, but keep deterministic evals.**

  * Use it for dimensions that assertions cannot judge well: completeness, clarity and whether the output resembles a good suitability report / Consumer Duty-style communication.
  * Calibrate it against human labels rather than treating the judge as ground truth.

* **Define stricter schemas between stages.**

  * Move towards Pydantic / JSON-schema contracts from unstructured evidence → observations → reconciled facts → actions / decisions → report.
  * The aim is to stop malformed state propagating silently while keeping the ingestion boundary flexible enough for new source shapes.

* **Run model bake-offs by task.**

  * Compare newer / stronger models separately on extraction and narrative, including factual accuracy, quality, cost and latency.
  * A stronger model may be worth paying for on difficult extraction or final narrative without replacing the cheaper model everywhere.

* **Support multimodal evidence properly.**

  * PDFs, scans, screenshots, spreadsheets and third-party platform statements should feed the same evidence model.
  * Preserve page / table / cell / excerpt provenance so the reviewer can check material facts against the original source.

* **Make provenance part of the review experience.**

  * The pipeline already stores source evidence internally; expose that directly alongside material figures and recommendations.
  * Ideally a reviewer can move from a report statement to the source that supports it.

* **Turn Human Review into a workflow rather than a footer.**

  * Review items should have type, evidence, severity and resolution state.
  * Material conflicts should be explicitly resolved before the report is considered final.

## Principle

* Use the model where interpretation or language is useful.
* Use code where the answer can be deterministic.
* Keep material facts tied to evidence.
* Given the assumed human-review step, surface uncertainty when the evidence does not support a confident answer.
