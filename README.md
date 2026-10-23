# SchedKo

Exam season is stressful enough. SchedKo takes your university's exam schedule PDF and tells you exactly when and where your exams are — no more hunting through 20 pages of tables.

## How it works

Upload the exam schedule PDF your university releases. Enter your course and year. SchedKo finds your exams and lays them out in a clean timetable.

## Why it exists

Our university releases exam schedules as a scanned 20-page PDF. Finding your own schedule means scrolling through hundreds of rows looking for your course. SchedKo does that for you.

## Status

Currently in development.

## PDF Processing 2.0

New processing jobs send the PDF directly to Gemini Flash-Lite. Two requests
extract separate page ranges using the complete document for table context.
Request starts are spaced at least 13 seconds apart and share local request,
token, and daily budgets. The default mode avoids converting pages into JPEGs.

Validated pages are checkpointed in PostgreSQL. Re-upload the same PDF after a
failed job or server restart to resume unfinished pages. Explicit cancellation
removes the job and its checkpoints. Schedule rows and the final ready status
are committed in one transaction, so partial jobs are not returned as results.
Completed schedules from the current extraction revision remain cached. After
this prompt update, re-uploading a PDF processed with the previous revision
offers fresh processing instead of returning its old cached extraction.

The shared prompt in `server/extraction_prompt.py` defines each field, separates
exam headers from class columns, and checks merge spans and true blanks. Unknown
cells remain null. A conflicting date is resolved only when one replacement has
at least two independent supporting cues in the document; unresolved dates stay
null. The direct-PDF response schema and validator restrict exam type to `Finals`,
`Midterms`, or null. These rules also guide the legacy image prompt, which only
has the current page as context. Versioned checkpoints prevent old prompt results
from being reused in new extraction attempts.

Use Python 3.11+, install `server/requirements.txt`, and restart the API after updating. Startup
creates the checkpoint table and adds progress columns to the existing files
table. No data-destructive migration is required. Run one Uvicorn worker: job
ownership and the shared limiter are maintained in one application process.

Server environment settings (in the untracked `server/.env`):

| Setting | Default | Purpose |
| --- | --- | --- |
| `PDF_PROCESSING_MODE` | `pdf` | Direct PDF pipeline; set `images` for the legacy image fallback |
| `PDF_EXTRACTION_STRATEGY` | `split` | Two page ranges; `full` uses one extraction range |
| `GEMINI_PDF_MODEL` | `gemini-3.5-flash-lite` | Model used by the direct PDF pipeline |
| `GEMINI_RPM` | `5` | Local maximum generation request starts per minute |
| `GEMINI_INPUT_TPM` | `250000` | Local input tokens per rolling minute, measured with `countTokens` |
| `GEMINI_RPD` | `20` | Local daily generation request allowance |

These defaults are local limits, not verified Google project quotas. Configure
them against the selected model's limits in AI Studio. All generation retries
consume the same allowance. The local daily counter resets at midnight Pacific
and is recreated on API restart; Google still enforces the actual project quota
across all applications and keys. The images fallback retains its original
per-page scheduling and does not use these direct-PDF budget settings.

The earlier local two-request experiment took 66.5 seconds for a 20-page scan.
That measurement excludes Render cold starts, browser upload, and database
commit; it is not a guaranteed production runtime. Scans can still contain
misread text or merged cells even when the response passes structural checks.
PDF Processing 2.0 is now the application's default, but a complete accuracy
benchmark and hosted end-to-end measurement are still outstanding.

Backend tests use synthetic PDFs and mocked Gemini/database responses:

```bash
python -m unittest discover -s server/tests -v
node --test client/tests/*.test.js
npm run lint
npm run build
```

Run Python commands using the virtual environment where backend requirements
were installed. Tests consume no Gemini quota. Live comparisons remain available
in [the benchmark tool](server/benchmarks/README.md), which now uses the same
extractor as the application.
