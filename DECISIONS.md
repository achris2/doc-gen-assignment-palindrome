# Decisions

- Started in `template_config.json`. Tighter prompts improved the reports but invented figures remained.
- Narrowing what each section was shown did not fix it. Every section was still reading the raw folder, so had to re-engineer the pipeline.
- The pipeline use patterns to classify the source files (whether it is a db, client meeting notes, etc). If those patterns miss, the LLM is a fallback option. The request and the database are then parsed in code when the shape is familiar. The meeting is extracted by the model. 
- The aim was to create schemas out of different formats of data in order to produce the right context to a very constrained LLM call. 
- Every section of the output report is written from the reconciled `case_facts`.
- The output quality is capped by that extraction. However, handing the raw sources back to the model would enhance the risk of hallucinations.
- The `case_facts` schema was built to reduce that risk by engineering the context of the LLM call. A stronger model may be able to extract the case facts but, to make it deterministic, the same checks would need to be run.
- Tax Implications is included only when the reconciled `selling` flag is true. The wording is fixed in code: a disposal may create a CGT liability, and the sentence always ends in `[REVIEW: CGT figure]`. No CGT amount is calculated. Every other section is always included. The model does not choose which sections appear.
- Getting a CGT figure wrong was seen as a "risk not worth taking", so leaving that to the human reviewer. 
- Assumed three core inputs: the request, the meeting note, and a CRM or db snapshot. 
- One file is kept per role to reduce the scope of the task. However, this means that a second meeting or snapshot could be dropped, and which file survives is filename order, not which meeting was later, which is a weakness. 
- The pipeline assumes a regular run cadence (i.e. a monthly or annual report, where there is "one of each evidence" as usual in annual reports) where the most recent fact would be the most relevant, not necessarily based on where it was found (i.e. a db entry could be less relevant than a figure mentioned in a meeting note)
- On account values the newer figure is selected. If any account value is undated, no value is chosen and the conflict is flagged for human review. 
- Assumed a human reviews before use, so a conflict flag and the source of the fact matter more than a client-ready letter in one pass. 
- No single ranking of the files, for that reason. The request is preferred for scope, selling, risk and the other instruction fields. The meeting is preferred for circumstances and objectives, the database for account identity. A source can be right on one field and wrong on another, so the disagreement stays visible.
- This is what the pipeline deliberately targeted. Making it more deterministic even if constraining it would likely lead to a more grounded but less "creative" report. 
- for `fde_notes.md` and `template_spec.md` the classifier is told to treat both as internal, so they are not meant to add a case fact. That depends on the classifier getting the role right.
- Screenshots are always demed to be noise. Market updates are noise when the classifier says so. 
- This is an important limitation, as a fact that exists only in an image, a note or a market pack is missed, and the pipeline generalises less. This is a trade-off to restrain the scope of the task.
- Stayed on `gpt-4o-mini` so runs stay comparable with the baseline and cheap enough to repeat. With the right context and instructions, it should be good enough. A stronger model would likely classify and write better. 
- Effectiveness sits in the schema and the checks, not in a larger model, and that comparison was not run because the focus was on improving the deterministic aspects of the pipeline first. 
- This is a fixed pipeline, rather than an agentic pipeline. Patterns classify known files, and one call classifies the rest.
- The request and the database are parsed in code unless the shape is unfamiliar.
- schema.py acts as the contract between stages of the pipeline. Dataclasses and literals define things like file roles, money types, individual facts, and the overall case structure. 
- This means the reporting stage works from a consistent case_facts object rather than having to interpret the raw input files again. 
- A meeting note is two calls, one for facts and one for decisions, and one further call writes the background summary. 
-Holdings, fees, tax wording and the recommendation lines are code. Code decides the order, the schema, which sections appear, and the checks. 
- That was a trade-off. Prompt tuning kept leaving case facts wrong, so those errors were taken off the model and put in t schema.
- However, it is likely that a different design or approach could have overcome those issues without taking away so many parts from the LLM call stack. 
- The eval is a small set of deterministic checks, aimed at the worst failures in terms of what getting those wrong would mean in the context of a FCA regulated suitability report.
- Examples: a missing FCA line or risk warning, a holdings id that does not match the case, a conflict that never reaches the footer, a tax section when nothing is being sold.
- A pass does not mean the report is client-ready or free of mistakes. The checks do not score tone, completeness, or whether the advice is right. That stays with the reviewer, hence the assumption of a human reviewer is crucial. 
- The original first stap in the plan was to limit what the model writes in the output report to minimise hallucinations.
- `template_config.json` still sets the section order and the fixed wording. The general prompt is only sent with the background summary, which is the one section the model fully writes. Holdings, fees, tax and the recommendations are filled from `case_facts` in code.
- The case itself is mixed. Known files, a familiar request and a familiar database are parsed in code. The model is the fallback when a file or a shape is not recognised, and it always extracts the meeting.
- With more time, generation would widen again: more sections would be written by the model, but constrained only from the grounded facts. That is deliberately narrow at the moment.

## With more time

- Make piepline more agentic, building the core agents within the pipeline but letting models call how to route them depending on the type of files it receives. 
- The pipeline already skips a stage when that file is absent, and it already falls back to the model when a request or database is not in the expected shape. It cannot take a path it was not written for. 
With more time, what arrived  a second meeting, a scan, a request that is not a table, would choose the route, and code would still own the schema and the checks.
- Ingest PDFs, scans and screenshots with provenance (likely through LLM calls) and turn review flags into items with type, evidence, severity and a resolution.
- Widen the evals past just 'critical' deterministic checks. 
- Have advisers / paraplanners label facts, conflicts, actions and review items, score extraction against those labels.
- Add a LLM as a judge for completeness, clarity and Consumer Duty tone, calibrated on the labels.
- The pipeline already uses dataclasses to define these schemas, essentially turning potentially messy, unstructured source data into structured case_facts with known fields and types. 
- This could be extended with something like Pydantic, which would make that boundary stricter by validating model output before it enters the case. For example, unsupported money types could be rejected explicitly rather than being dropped or converted into empty values.