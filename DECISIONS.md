# Decisions 

(PROVISIONAL NOTES TO HELP KEEP TRACK / WRITE FINAL DECISIONS.MD)

## initial notes / ideas
- starter pipeline dumps all files into every llm call, in these cases some files are noise, potentially degrading output and inflating tokens (e.g. platform market updates)
- split into deterministic aspects of pipeline (e.g. which files enter llm context, or any warnings)
- general market / portfolio pack material does not appear relevant for outputs 
- meeting_notes, adviser meeting important in the context of fca regulated firm, data may be more recent than db
- conflicts between sources must be flagged (e.g. `[REVIEW]`)
- risk warning language must not be subject to change by LLM 

## approach
- assume core info exists, even if in different formats or shapes (crm/db, meeting notes, request forms), so no strict schema and getting LLM to help classify info 
- create a 4-step pipeline: `Retrieve → Extract → Reconcile → Write`
  - **Retrieve:** classify files into types (`request`, `meeting`, `db`, `noise`, `internal`) via deterministic rules first, with LLM fallback; ignore complex noise/images for v0
  - **Extract:** parse DB json and request tables directly in code; use structured LLM call only on meeting notes to pull raw observations with provenance (`field`, `value`, `source_file`, `as_of`)
  - **Reconcile:** enforce authority rules in code (request=scope, meeting=live actions, DB=identity); pick newest date when numbers conflict, never average, and force missing fees/CGT to `[REVIEW]`
  - **Write:** code injects facts, tables, and fixed FCA risk warnings; LLM only writes narrative prose using reconciled facts
- **HITL & provenance logic:** in a regulated space, advisers cannot rely on "black-box" outputs. every extracted fact must carries provenance (`source_file`, `date`, `field`) so the human reviewer has full traceability to audit figures back to source documents before signing off


## issues still to work on

- **prompt tuning for new setup:** update and refine narrative prompts so they work cleanly with reconciled facts rather than relying on old raw context formats
- **complex conflict resolution:** system is still weak on multi-source discrepancies (e.g. meeting notes inventing phantom account IDs like `joint_GIA` instead of joining existing DB accounts)
- **strict data hierarchy & recency:** enforce a clearer source hierarchy where newer evidence explicitly overrides older data (e.g. fresh meeting notes over stale DB entries)
- **noise control:** noise files are mostly kept out of context, but need to lock down edge cases so random attachments or packs don't bleed through
- **edge case handling (client_04):** capture complex terms (earnout, bridging loans) as explicit `[REVIEW]` items and automatically drop closed accounts from holdings