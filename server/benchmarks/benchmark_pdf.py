"""Measure the candidate before integrating it. See README.md in this directory."""

import argparse
import asyncio
from collections import Counter
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time
import unicodedata

import httpx
from dotenv import dotenv_values
from pypdf import PdfReader
from pypdf.errors import PdfReadError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.pdf_extraction import (
    ALL_FIELDS, MODEL, ExtractionError, PdfExtractor, QuotaExhausted, RequestLimiter,
)


def inspect_pdf(path):
    size = path.stat().st_size
    if size > 50_000_000:
        raise ValueError("PDF exceeds Gemini's 50 MB document limit.")
    contents = path.read_bytes()
    try:
        reader = PdfReader(BytesIO(contents))
    except PdfReadError as exc:
        raise ValueError("The input is not a readable PDF.") from exc
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported by this benchmark.")
    page_count = len(reader.pages)
    if not 1 <= page_count <= 1000:
        raise ValueError("Expected a PDF with 1–1000 pages.")
    return contents, {"sha256": hashlib.sha256(contents).hexdigest(), "bytes": size,
                      "pages": page_count}


def normalized(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Reference row values must be strings or null.")
    # Case and whitespace differences are harmless; preserve punctuation and digits.
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def row_counts(rows):
    if not isinstance(rows, list):
        raise ValueError("Each reference page must contain a list of rows.")
    result = Counter()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(ALL_FIELDS):
            raise ValueError("Every reference row must contain exactly the 13 schedule fields.")
        result[tuple(normalized(row[key]) for key in ALL_FIELDS)] += 1
    return result


def load_reference(path, pdf):
    if path is None:
        return None
    data = json.loads(path.read_text())
    if data.get("pdf_sha256") != pdf["sha256"] or data.get("manually_verified") is not True:
        raise ValueError("Reference must match this PDF and be marked manually_verified=true after review.")
    pages = data.get("pages", {})
    if set(pages) != {str(page) for page in range(1, pdf["pages"] + 1)}:
        raise ValueError("Reference must cover every PDF page, including empty cover pages.")
    for rows in pages.values():
        row_counts(rows)
    return {int(page): rows for page, rows in pages.items()}


def compare_pages(actual, reference):
    if reference is None:
        return {"verified": False, "reason": "No manually verified reference supplied."}
    differences = []
    for page in sorted(set(actual) | set(reference)):
        observed = row_counts(actual.get(page, []))
        expected = row_counts(reference.get(page, []))
        missing, extra = sum((expected - observed).values()), sum((observed - expected).values())
        if missing or extra or page not in actual or page not in reference:
            differences.append({"page": page, "missing_rows": missing, "extra_rows": extra})
    return {"verified": not differences, "differences": differences}


def select_strategy(runs):
    medians = {}
    for strategy in ("full", "split"):
        group = [run for run in runs if run["strategy"] == strategy]
        if len(group) >= 3 and all(
            run.get("success") and run["accuracy"]["verified"] and run["seconds"] <= 180
            for run in group
        ):
            medians[strategy] = statistics.median(run["seconds"] for run in group)
    # Both configurations must be evaluated to apply the selection rule fairly.
    if any(sum(run["strategy"] == strategy for run in runs) < 3 for strategy in ("full", "split")):
        return None
    if not medians:
        return None
    fastest = min(medians, key=medians.get)
    if "full" in medians and medians["full"] < medians[fastest] * 1.1:
        return "full"
    return fastest


def save_private(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def current_rss_kib():
    """Linux RSS, independent of a launcher's inherited high-water mark."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


async def benchmark(args):
    contents, pdf = inspect_pdf(args.pdf)
    reference = load_reference(args.reference, pdf)
    if args.inspect:
        print(json.dumps({"pdf": pdf, "reference_available": reference is not None}, indent=2))
        return 0
    api_key = os.getenv("GEMINI_API_KEY") or dotenv_values(args.env_file).get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Set GEMINI_API_KEY in server/.env or the environment. Never pass it as a CLI argument.")
    if args.probe:
        # Bounded generation attempts to discover feasibility/error metadata, not
        # a claim that the project's quotas have been verified.
        allowance = 2 if args.probe_strategy == "split" and pdf["pages"] > 1 else 1
        limiter = RequestLimiter(5, 1_000_000_000, allowance)
        configurations = [(args.probe_strategy, 1)]
    else:
        if not args.quota_verified or any(value is None for value in (args.rpm, args.tpm, args.remaining_requests)):
            raise ValueError("Supply verified --rpm, --tpm, --remaining-requests and --quota-verified, or use --probe for one attempt.")
        limiter = RequestLimiter(args.rpm, args.tpm, args.remaining_requests)
        # Alternate strategies to reduce bias from provider load at one instant.
        configurations = [(strategy, index) for index in range(1, args.runs + 1)
                          for strategy in ("full", "split")]
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="schedko-benchmark-"))
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    report = {"pdf": pdf, "model": args.model, "quota_verified": bool(args.quota_verified and not args.probe),
              "runtime": args.runtime, "timing_scope": "PDF preparation, upload, extraction, validation; excludes browser upload and DB commit",
              "initial_process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "initial_current_rss_kib": current_rss_kib(),
              "runs": [], "selected_strategy": None, "production_gate_passed": False}
    print(f"Benchmark artifacts: {output}", flush=True)
    async with httpx.AsyncClient(timeout=150) as client:
        for strategy, index in configurations:
            events = []
            extractor = PdfExtractor(client, api_key, limiter, model=args.model, events=events)
            record = {"strategy": strategy, "run": index, "success": False,
                      "accuracy": {"verified": False}, "events": events}
            report["runs"].append(record)
            started = time.monotonic()
            async def sample_memory():
                while True:
                    rss = current_rss_kib()
                    if rss is not None:
                        record["sampled_peak_rss_kib"] = max(record.get("sampled_peak_rss_kib", 0), rss)
                    await asyncio.sleep(0.05)

            memory_task = asyncio.create_task(sample_memory())
            print(f"Starting {strategy} run {index}", flush=True)
            exhausted = False
            try:
                # Include preparation and upload in every timed run, not just the first.
                contents, current_pdf = await asyncio.to_thread(inspect_pdf, args.pdf)
                if current_pdf != pdf:
                    raise ValueError("PDF changed during the benchmark.")
                async with extractor.uploaded_pdf(contents) as uri:
                    pages = await extractor.extract(uri, pdf["pages"], strategy)
                    record["accuracy"] = compare_pages(pages, reference)
                    record["seconds"] = time.monotonic() - started
                    record["rows"] = sum(len(rows) for rows in pages.values())
                    record["success"] = True
                save_private(output / f"{strategy}-{index}-review.json", {
                    "pdf_sha256": pdf["sha256"], "manually_verified": False, "pages": pages,
                })
            except QuotaExhausted as exc:
                record["error"] = str(exc)
                exhausted = True
            except ExtractionError as exc:
                record["error"] = str(exc)
            finally:
                memory_task.cancel()
                await asyncio.gather(memory_task, return_exceptions=True)
                record.setdefault("seconds", time.monotonic() - started)
                record["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                report["remaining_local_request_allowance"] = limiter.remaining
                report["selected_strategy"] = select_strategy(report["runs"])
                save_private(output / "report.json", report)
            print(json.dumps({key: value for key, value in record.items() if key != "events"}), flush=True)
            if exhausted or limiter.remaining <= 0:
                break
    # Even a passing extraction benchmark still needs hosted end-to-end validation.
    report["candidate_gate_passed"] = report["selected_strategy"] is not None and report["quota_verified"]
    save_private(output / "report.json", report)
    print(json.dumps({"candidate_gate_passed": report["candidate_gate_passed"],
                      "selected_strategy": report["selected_strategy"],
                      "report": str(output / "report.json")}), flush=True)
    return 0 if report["candidate_gate_passed"] else 2


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pdf", required=True, type=Path)
    result.add_argument("--reference", type=Path)
    result.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    result.add_argument("--inspect", action="store_true", help="Inspect PDF/reference without network access")
    result.add_argument("--probe", action="store_true", help="One bounded trial; cannot pass the benchmark gate")
    result.add_argument("--probe-strategy", choices=("full", "split"), default="full")
    result.add_argument("--quota-verified", action="store_true")
    result.add_argument("--rpm", type=int)
    result.add_argument("--tpm", type=int)
    result.add_argument("--remaining-requests", type=int, help="Verified remaining daily budget allocated to this benchmark")
    result.add_argument("--runs", type=int, default=3)
    result.add_argument("--model", default=MODEL)
    result.add_argument("--runtime", default="local", help="Label the actual runtime, e.g. render-free; does not simulate it")
    result.add_argument("--output-dir", type=Path, help="Private directory outside Git; defaults to a temporary directory")
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be positive")
    try:
        raise SystemExit(asyncio.run(benchmark(args)))
    except (ValueError, OSError, ExtractionError):
        # Never print an untrusted exception containing an API URL or document data.
        print("Benchmark could not start/complete. Check the PDF, reference, credentials, and quota arguments.", file=sys.stderr)
        raise SystemExit(1)
