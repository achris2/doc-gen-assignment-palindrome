# Decisions 

## initial notes / ideas
- starter pipeline dumps all files into every llm call, in these cases some files are noise, potentially degrading output and inflating tokens (e.g. platform market updates)
- split into deterministic aspects of pipeline (e.g. which files enter llm context, or any warnings)
- general market / portfolio pack material does not appear relevant for outputs 
- meeting_notes, adviser meeting important in the context of fca regulated firm, data may be more recent than db
- conflicts between sources must be flagged (e.g. `[REVIEW]`)
- risk warning language must not be subject to change by LLM 