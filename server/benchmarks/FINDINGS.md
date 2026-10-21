# Initial feasibility findings — 2026-10-21

The requested PDF was inspected locally: 20 scanned pages, approximately
9.3 MB, with no embedded text. It was not copied into the repository.

Three bounded live trials used `gemini-3.5-flash-lite` and the provided key.
These were local extraction measurements, not Render end-to-end measurements.

| Trial | Generation requests | Result latency | Returned rows | Accuracy status |
| --- | ---: | ---: | ---: | --- |
| Full document, initial prompt | 1 | 136.94 s | 594 | Failed spot check: shifted columns and a missing row |
| Full document, explicit source-column mapping | 1 | 84.06 s | 595 | Failed spot check: incorrect merged/blank-cell values |
| Two physical page ranges, corrected prompt | 2 | 66.50 s | 595 | Failed spot check: incorrect building and merged-cell values |

The split trial started generations 13 seconds apart. Its generation durations
were 52.53 and 49.25 seconds, overlapping after the scheduled delay. Upload took
3.51 seconds. Each generation counted approximately 11,520 input tokens because
both received the full PDF for context. All four generation attempts returned
HTTP 200; none returned rate-limit metadata. No retries were used.

The observed timings meet the extraction-only target, but **neither strategy
passes the accuracy gate**. Equal row counts do not prove correct schedules.
The first response mistakenly retained the source student-count column and
shifted subsequent fields; the updated prompt explicitly skips it, and a
validation rule now rejects numeric student counts in person-name fields.
Remaining errors require further extraction changes and a reviewed reference.

The API key permitted model discovery and generation. Project RPM/TPM ceilings
and remaining daily quota were not exposed by those successful responses, and
there was no connected browser session to inspect AI Studio. The probe caps
are not represented as verified project quotas. No billing settings changed.

The initial raw process high-water memory reading was 494,144 KiB, already
present immediately after upload and identical across trials. This has not been
established as the candidate's incremental memory requirement or Render usage.
The benchmark now records startup memory and samples live Linux RSS to separate
launch/runtime effects from extraction. Actual hosted memory still needs testing.

## Decision

Do not integrate or enable the candidate in production yet. The approved plan
requires an accurate benchmark before integration. Stop the six-run comparison
early because manual spot checks have already disproved correctness; do not
consume more quota merely to time known-invalid configurations.

Next work is to improve merged-cell/layout handling, create a fully reviewed
reference outside Git, verify project quotas in an authenticated AI Studio
session, and repeat the comparison. Only a passing candidate should proceed to
durable checkpoints, atomic publication, UI progress integration, and a full
Render upload-to-database latency/memory check.

Raw reports and review files are private temporary artifacts under
`/tmp/schedko-live-benchmark`, `/tmp/schedko-live-benchmark-v2`, and
`/tmp/schedko-live-benchmark-split` in the working environment. They are not
committed, and the extracted review files are explicitly marked unverified.
