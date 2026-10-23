# Direct-PDF extraction benchmark

This CLI compares extraction strategies using the shared application engine in
`server/pdf_extraction.py`. The API now defaults to PDF Processing 2.0; the CLI's
accuracy/performance gate remains a benchmark result, not a deployment switch.

The benchmark compares the full PDF in one generation against two overlapping
generations, each assigned half the physical pages. Both see the same complete
PDF to resolve merged cells and section headers. The PDF is uploaded once per
run; the remote file is deleted afterward, including on extraction failure or
cancellation. The default model is `gemini-3.5-flash-lite` with minimal thinking.

Reading instructions are shared in `server/extraction_prompt.py`. Prompt revision
2 explicitly defines metadata and column boundaries, limits merged-cell carry,
and provides null fallbacks and conservative source-conflict checks. Exam type
is constrained by both the direct-PDF response schema and local validation.
Checkpoint and upload-cache revisions change with this update so older results
are not treated as new-prompt extractions. Prompt instructions and valid schema
output do not establish accuracy; compare each trial with an independent source
review. The standalone probe always performs a fresh extraction without API/DB
checkpoints.

## Setup

Use a Python 3.11+ virtual environment (no PostgreSQL or running API required):

```bash
python -m venv /tmp/schedko-benchmark-venv
/tmp/schedko-benchmark-venv/bin/python -m pip install -r server/benchmarks/requirements.txt
```

Store `GEMINI_API_KEY` in the ignored `server/.env`, or set it in the process
environment. Do not put keys in command arguments. Never commit exam PDFs,
extracted rows, or reference files. The output directory defaults to a private
temporary directory; JSON artifacts are created with mode 0600.

Inspect a PDF without uploading or consuming API quota:

```bash
/tmp/schedko-benchmark-venv/bin/python server/benchmarks/benchmark_pdf.py \
  --pdf /absolute/path/schedule.pdf --inspect
```

## Bounded feasibility probe

```bash
/tmp/schedko-benchmark-venv/bin/python server/benchmarks/benchmark_pdf.py \
  --pdf /absolute/path/schedule.pdf --probe
```

The default probe permits **one generation attempt**. Add `--probe-strategy split`
to permit two total attempts for the two page ranges, spaced at least 13 seconds
apart. Those are local caps, not assertions about actual quota availability.
Retries or range splitting cannot exceed the cap. Upload, token counting, and
cleanup make separate non-generation requests. It cannot pass the benchmark gate and
does not assert that project quotas were verified. It can produce a review file
for building a reference and a redacted timing/usage report.

## Accuracy reference

A successful run writes `full-1-review.json` (or the corresponding strategy/run).
This contains the PDF SHA-256, `manually_verified: false`, and a `pages` object
mapping physical page numbers to lists of expanded 13-field schedule rows.
Cover pages must be represented with empty row lists. Review **every row and
every field against the PDF**, correct omissions/duplicates/merged cells, and
only then set `manually_verified` to `true`. Keep this file outside Git.

Reference comparison is per page, preserves duplicate multiplicity, and checks
all 13 fields. Only Unicode compatibility, letter case, and whitespace are
normalized. Missing values and conflicting dates/rooms are not silently fixed.
Agreement between two model runs alone is not a verified reference.

## Offline source-image accuracy audit

`verify_accuracy.py` checks a saved extraction against independently reviewed
cells without additional API requests. Use it to record partial review honestly:
matching counts or a few matching fields cannot pass its all-correct gate.

```bash
/tmp/schedko-benchmark-venv/bin/python server/benchmarks/verify_accuracy.py \
  --pdf /absolute/path/schedule.pdf \
  --extraction /tmp/schedko-comparison/split-1-review.json \
  --review /absolute/path/private-source-review.json \
  --output /tmp/schedko-accuracy-report.json
```

The separate review file has this shape (synthetic example):

```json
{
  "pdf_sha256": "SHA256_OF_REVIEWED_PDF",
  "reviewed_against_page_images": true,
  "row_counts": {"1": 0, "2": 1},
  "checks": [
    {"page": 2, "row": 1, "expected": {"subject": "TEST 101", "exam_building": "CAB"}}
  ],
  "source_corrections": []
}
```

Page and row coordinates are 1-based and follow source order. Transcribe expected
values from rendered page images, not from the model response. Record explicitly
authorized corrections to source errors in `source_corrections` and use the
corrected value in the affected checks. The metadata list documents a correction;
it does not apply an override to the extraction or to the application database.

Both inputs must match the PDF hash. The report tracks reviewed and unreviewed
fields, per-field failures, page coverage, and row counts. A pass requires reviewed
counts for **every page**, all 13 fields of every expected row reviewed, no
missing/extra rows or pages, and no cell mismatches. Duplicate checks cannot
inflate coverage. The example above is deliberately partial and cannot pass.

Formatting comparison accepts case/Unicode whitespace, spacing within subject,
course, room and building identifiers, equivalent parseable dates/times,
`AY` prefixes, and listed exam/semester aliases. It preserves identifier digits
and punctuation and distinguishes missing cells from the literal string `"null"`.
This formatting comparison is separate from the stricter full-reference benchmark
comparison above. Reordered rows fail the source-order audit and need review.

Detailed reports contain document contents and are written with mode 0600; keep
them and the source review outside Git. Standard output contains aggregate counts.
Exit 0 means a complete match, 2 means a mismatch or incomplete review, and 1 means
invalid input. See [the latest accuracy findings](ACCURACY_RESULTS.md).

## Full comparison

Read this model's actual project RPM, input TPM, and remaining daily request
allowance in [AI Studio](https://aistudio.google.com/usage?tab=rate-limit).
The model-list API reports model context limits, **not project rate limits**.
Avoid concurrent use of the same project during the benchmark. The allowance
supplied here is allocated to this process and includes failed attempts; it
does not query other applications' consumption or reset at midnight.

```bash
/tmp/schedko-benchmark-venv/bin/python server/benchmarks/benchmark_pdf.py \
  --pdf /absolute/path/schedule.pdf \
  --reference /absolute/path/manually-reviewed.json \
  --rpm "$BENCH_RPM" --tpm "$BENCH_TPM" \
  --remaining-requests "$BENCH_REQUEST_ALLOWANCE" --quota-verified \
  --runtime render-free --output-dir /tmp/schedko-comparison
```

Only label the runtime `render-free` when actually executing there; the label
does not simulate Render hardware. By default, three trials of each strategy
alternate, requiring at least nine successful generation requests, plus retries
or splits. The limiter spaces starts by at least 13 seconds, uses a rolling
60-second token budget from `countTokens`, and allows at most two generations
in flight. Both requests include the full document's input tokens. All retries
use the same limiter and honor provider cooldowns. Daily exhaustion stops the
comparison. Permanent errors do not retry.

Malformed/truncated output splits only the affected range. A failing single
page fails the run. A 150-second deadline applies to each generation attempt;
at most three transient-error attempts are allowed. A fallback may take longer
than the target and therefore fail the performance gate.

## Reading results and the integration gate

`report.json` records preparation/upload/extraction timing, generation waits,
HTTP statuses, token usage, page/row differences, and peak process RSS on Linux.
It does not include API keys, provider error bodies, or extracted rows.
Peak RSS is a process-wide high-water mark, not an independent per-run sample.
The report also records the initial high-water mark and current Linux RSS,
and samples current RSS every 50 ms during a run. Use `sampled_peak_rss_kib`
when a launcher's inherited high-water mark exceeds the measured process usage;
sampling can miss brief spikes. Confirm memory independently on the actual host.

`candidate_gate_passed` requires verified quotas, three trials of **both**
configurations, and at least one strategy whose every trial is accurate and
finishes within 180 seconds. Choose the passing strategy with the lowest median;
prefer one request when it is less than 10% slower. Without a fully reviewed
reference, the gate remains false. Exit code 2 means the gate did not pass;
exit code 1 indicates an input/setup failure.

Timing includes reading the PDF, uploading it to Gemini, extracting rows, and
validating them. Remote cleanup is recorded separately from the result latency.
It excludes browser-to-backend transfer, Render cold starts, and database commit.
Consequently `production_gate_passed` remains false in this standalone tool.

The API integrates the split strategy, durable page checkpoints, and atomic
publication. See the root README for configuration and fallback settings.
The historical trials did not pass the accuracy gate. A fully reviewed reference
and the full hosted path through database commit still need measurement before
advertising a 1–3 minute runtime or claiming extraction accuracy is verified.

## Tests

```bash
/tmp/schedko-benchmark-venv/bin/python -m unittest discover \
  -s server/tests -p test_pdf_benchmark.py -v
```

Tests use synthetic rows and mocked HTTP transport and consume no API quota.
Run `python -m unittest discover -s server/tests -v` to include the offline
accuracy-gate regressions as well as the application processing tests.

Protocol references: [Files API](https://ai.google.dev/api/files),
[generateContent](https://ai.google.dev/api/generate-content),
[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).
