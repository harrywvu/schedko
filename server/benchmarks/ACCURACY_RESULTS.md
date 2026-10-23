# Accuracy audit — 2026-10-23

**Result: FAIL. PDF Processing 2.0 does not meet the requirement that every exam
schedule be correct.** The source-image checks already establish errors; the
remaining unreviewed fields prevent any claim of a complete accuracy percentage.

## Refined prompt follow-up

The prompt is now shared by the direct-PDF and legacy image paths in
`server/extraction_prompt.py`. It explicitly defines exam type, maps each column,
checks merge boundaries and true blanks, preserves uncertain rows with null
cells, and gives a conservative process for resolving date conflicts using at
least two independent document cues. The image path is told that only its current
page is available. The direct-PDF schema and local validator additionally reject
exam types other than `Finals`, `Midterms`, or null.

Checkpoint prompt revision is now 2; upload-cache revision is 3. Interrupted jobs
do not reuse previous-prompt checkpoints. Re-uploading an older completed PDF
offers processing with the new prompt; it does not silently serve its old cache.
No stored production schedule was modified during these local trials.

Two bounded split trials used the same model, minimal thinking, request spacing,
PDF, and independent 3,638-cell source review as the baseline. Each made two
generation requests. Both returned 595 rows with every per-page count matching.
The first refined trial took 60.34 seconds, matched all subjects and exam types,
but retained the 21 conflicting dates and had seven other reviewed mismatches.
The final revision strengthened date-list consistency and column self-checks:

| Reviewed field | Baseline errors | Final prompt errors |
| --- | ---: | ---: |
| Subject (595 checks) | 2 | 0 |
| Exam date (595 checks) | 21 | 0 |
| Exam time (595 checks) | 0 | 0 |
| Exam type (595 checks) | 595 | 0 |
| Semester (595 checks) | 0 | 0 |
| Academic year (595 checks) | 0 | 0 |
| Course/year (2 selected checks) | 2 | 1 |
| Room (33 selected checks) | 1 | 4 |
| Building (33 selected checks) | 7 | 5 |
| **Total reviewed mismatches** | **628** | **10** |

Final-trial latency was **59.55 seconds**, excluding browser transfer, Render
cold starts and DB publication. All page-18 dates matched the user's May 11
correction through the generic document-conflict instructions; no PDF-specific
date or page number was placed in the production prompt.

The remaining ten mismatches include a person's name in course/year, wrong
buildings, and guessed letter values in blank room cells. Room mismatches
increased in this response, so the improvement is not uniform. Prompt instructions
do not enforce every fallback: this is still a failed accuracy audit, not a
guarantee of full correctness. The same 4,097 fields remain unreviewed. Counts
compare the same reviewed cells across individual stochastic responses, not an
unbiased estimate of total accuracy or a repeated-run performance guarantee.

The final backend suite has **83 passing tests** (five added for exam-type
constraints, preservation of unknown cells, invalid-checkpoint recovery and
old-cache reprocessing). No frontend behavior changed in this refinement.
`git diff --check` passes. The earlier frontend checks below remain the last
frontend verification, not a newly measured hosted flow.

Private reports are under `/tmp/schedko-accuracy/prompt-v2/` and
`/tmp/schedko-accuracy/prompt-v2-final/`. Each has `report.json`,
`split-1-review.json`, and `accuracy-report.json`. The saved model outputs remain
marked unverified; the offline audit exits 2 for both refined trials.

## Baseline live extraction

The root PDF has 20 scanned pages and no embedded text on any page. The previous
shared application extraction prompt was run locally with the production default
split strategy and model, without saved checkpoints. It returned 595 rows in
62.07 seconds using two generation requests, with no retries or rate-limit errors.
Timing includes PDF preparation, upload, generation and validation; it excludes
browser upload, hosted cold starts and database publication. This was not a
Render end-to-end test.

The PDF hash was
`eb050ef02e19b0fb1bffe26fb12ac5dde2b7013dac02d3602fd9cc2a2623058d`.
Results describe this one live response; future generations may differ.

## Verification against page images

All 20 rendered pages were inspected. Source table rules helped count rows;
manual review accounted for the second section header on page 7 and missing
detected table borders on pages 9 and 15. All 20 per-page counts match, including
the empty cover. This verifies counts, not row identities or duplicate handling.

The independent review checks subject and all five section metadata fields for
every row, plus 68 selected location/course cells. It covers 3,638 of the 7,735
expanded cells; 4,097 remain unreviewed. Selected cells target visible problems,
merged/blank cells and bottom rows, so their error rates are not population
estimates. Instructor, examiner, class time and class day values have not been
fully transcribed into a reference.

| Field | Checked | Matched | Mismatched |
| --- | ---: | ---: | ---: |
| Subject | 595 | 593 | 2 |
| Exam date | 595 | 574 | 21 |
| Exam time | 595 | 595 | 0 |
| Exam type (`major_exam`) | 595 | 0 | 595 |
| Semester | 595 | 595 | 0 |
| Academic year | 595 | 595 | 0 |
| Course/year, selected cells | 2 | 0 | 2 |
| Room, selected cells | 33 | 32 | 1 |
| Building, selected cells | 33 | 26 | 7 |
| **Total reviewed** | **3,638** | **3,010** | **628** |

Observed errors include:

- Every row's exam type contains a subject code instead of `Finals`. The prompt
  names `major_exam` but does not explain its meaning; the response is structurally
  valid, so the current schema validation accepts it.
- Page 3 rows 27–28 carry forward the preceding subject instead of reading the new
  merged subject cell.
- A course/year cell on page 10 contains a person's name, while another misreads
  the program code. Column mapping can still shift despite the student-count fix.
- Locations include incorrect buildings, a missed merged building value, and an
  invented room number where the source cell is blank.
- All 21 page-18 rows use the source's conflicting printed March date. The user
  explicitly selected **May 11, 2026** for verification. That correction is recorded
  in the private reference; it has not been applied as an application override.

The source's printed date conflict is distinct from the model's other extraction
errors. No document-specific correction has been hardcoded into the shared engine.

## Regression checks and artifacts

Added `verify_accuracy.py` and 17 synthetic accuracy-gate tests. They cover
partial-review rejection, matching counts with wrong cells, duplicate/missing
rows, source date corrections, missing/extra pages, hash binding, null-string
mistakes and invalid review inputs. Only a complete matching review can return
`all_correct: true`. These tests verify the audit, not Gemini's OCR accuracy.

Verification completed:

- Backend suite: **78 passed** (61 existing plus 17 new).
- Frontend test files: **2 passed** using `node --test client/tests/*.test.js`.
- Frontend ESLint: passed.
- Frontend production build: passed, with dependency `eval` and bundle-size warnings.
- Offline live-response audit: exit **2**, `all_correct: false`, with all 20
  row-count checks passing and the 628 reviewed-cell mismatches above.

Private artifacts are outside Git under `/tmp/schedko-accuracy/`:

- `baseline/report.json`: timing and request metadata.
- `baseline/split-1-review.json`: unverified model output.
- `source-review.json`: independent, partial source review and user date correction.
- `accuracy-report.json`: detailed comparison and coverage.
- `pages/`: rendered source pages.

The baseline audit added verification and findings without changing production
extraction behavior. The follow-up above refines the production prompt and cache
revisions, but does not repair stored schedules. Before claiming complete accuracy, improve
metadata/column and merged-cell handling, provide a deliberate way to resolve
source conflicts, finish the independently reviewed 13-field reference, and
repeat the live extraction and full hosted flow against it.
