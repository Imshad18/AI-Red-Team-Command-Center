# AI Red Team Command Center

A local-first command center for AI red-team competitions and agent-security work. It imports an existing archive of competition folders, extracts useful structure, finds similar historical cases, maps objectives and trust boundaries, ranks attack directions, generates tailored competition prompt variants, and learns from manually logged outcomes.

The analyzer is deterministic. It does **not** call OpenAI, Anthropic, Gemini, Ollama, or any other AI service.

## What it includes

- Recursive archive import without modifying originals
- Automatic competition inference from folder layout
- SHA-256 duplicate detection
- Text, Markdown, code, JSON/JSONL, CSV, HTML, XML, logs and config ingestion
- Built-in DOCX, PPTX and XLSX text extraction using Python's standard library
- PDF indexing with optional text extraction when `pypdf` or `pdftotext` is already installed
- Screenshot/image evidence indexing
- Outcome detection: success, partial, failure, unknown
- Technique detection and auto-built technique library
- Deterministic objective analyzer
- Goal, success criteria, constraints, assets, tools and trust-boundary extraction
- Identity/account/authorization/privacy/tool-scope/external-content boundary mapping
- Ranked attack-direction suggestions
- Historical similarity search across the imported archive
- Prompt Forge with tailored competition prompts for each direction
- Attempt logging so future scoring uses your own success history
- Evidence view
- Competition and technique statistics
- Light, Dark and Red themes
- Local SQLite database
- Binds only to `127.0.0.1`
- No external Python packages required for the base build

## Run on Windows

1. Install Python 3.10+ if it is not already installed.
2. Download or clone this repository.
3. Double-click `run.bat`.
4. The Command Center opens at `http://127.0.0.1:8765`.

Your local database is created at:

`data/command_center.db`

## First use

Open **Import Archive**, select the root folder containing your previous competitions, then click **Import / Update**.

Example:

```text
D:\AI-Red-Team\
    Gray-Swan-August\
    Gray-Swan-Safeguards\
    HBAR\
    Private-Gig-01\
    Trajectory\
```

The importer keeps original files untouched and stores the searchable index in SQLite.

Then open **New Challenge**, paste the full objective/rules/tool description, and click **Analyze deeply**.

## Analyzer pipeline

The non-AI analyzer runs several deterministic passes:

1. Normalize and segment the objective.
2. Extract likely goal and success conditions.
3. Detect explicit restrictions and negative constraints.
4. Identify tools, assets and external side effects.
5. Map identity, account, authorization, consent, privacy, tool-scope and external-content boundaries.
6. Detect known AI-agent attack surfaces.
7. Compare the task with historical archive material.
8. Apply your own technique success history to direction scoring.
9. Generate a coverage map and ranked directions.
10. Feed the selected direction into Prompt Forge.

## Prompt Forge

Prompt Forge creates variants from rules and extracted objective features. It does not use a language model. Current families include:

- Workflow continuation
- Delegated approval
- Boundary split / multi-step decomposition
- Object and parameter binding
- Tool-result trust
- Indirect content instructions
- Verification gaps
- Format/parser boundaries
- Ambiguous reference resolution
- Goal-preserving alternative workflows
- Policy-scope challenges
- Persistent state carryover

Generated prompts are intended for authorized AI red-team competitions and sandbox testing.

## Privacy

- Server listens on `127.0.0.1` only.
- No telemetry is included.
- No external AI API is called.
- Imported files are read, hashed and indexed; they are not edited.
- The database stays inside the local project folder.

## Tests

Run:

```bash
python -m unittest discover -s tests -v
```

## Repository layout

```text
app.py
core/
  analyzer.py
  db.py
  importer.py
  promptgen.py
  search.py
web/
  index.html
  styles.css
  app.js
tests/
run.bat
run.sh
```

## Roadmap

Useful next additions include screenshot OCR, graph visualization of actor/tool/data flows, structured importers for specific competition export formats, automatic prompt/response pair recovery from chat logs, per-competition coverage matrices, prompt mutation rules learned from successful historical attempts, archive backups, and encrypted local storage.
