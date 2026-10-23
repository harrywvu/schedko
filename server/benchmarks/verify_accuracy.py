"""Audit saved extraction cells against a separate, visually reviewed source.

No API calls. A partial review can disprove accuracy, but cannot certify it.
Keep review and output files outside Git: they contain document contents.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

from pypdf import PdfReader

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.benchmark_pdf import save_private
from pdf_extraction import ALL_FIELDS
from parsing import parse_exam_date, parse_time_range


def canonical(field, value):
    """Accept formatting differences, never substitute guessed OCR characters."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Cells must be text or null.")
    value = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    value = value.replace("–", "-").replace("—", "-")
    if field == "exam_day":
        parsed = parse_exam_date(value)
        return parsed.isoformat() if parsed else value
    if field in ("exam_time", "class_time"):
        parsed = parse_time_range(value)
        return parsed if parsed else value
    if field in ("subject", "course_year", "exam_room", "exam_building"):
        return value.replace(" ", "")
    if field == "academic_year":
        return re.sub(r"^ay\s*", "", value)
    if field == "major_exam":
        return {"final": "finals", "final examination": "finals",
                "midterm": "midterms", "midterm examination": "midterms"}.get(value, value)
    if field == "semester":
        return {"second": "second semester", "2nd semester": "second semester",
                "first": "first semester", "1st semester": "first semester"}.get(value, value)
    return value


def page_number(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("Page keys must be positive decimal strings.")
    number = int(value)
    if number < 1 or str(number) != value:
        raise ValueError("Page keys must be canonical positive integers.")
    return number


def audit(extraction, review, pdf_sha256, page_count):
    for document in (extraction, review):
        if not isinstance(document, dict) or document.get("pdf_sha256") != pdf_sha256:
            raise ValueError("Extraction and review must match the input PDF hash.")
    if review.get("reviewed_against_page_images") is not True:
        raise ValueError("Review must be checked against page images independently of extraction.")
    expected_pages = set(range(1, page_count + 1))
    counts = review.get("row_counts")
    if not isinstance(counts, dict):
        raise ValueError("Review must supply row_counts.")
    counts = {page_number(key): count for key, count in counts.items()}
    if not set(counts) <= expected_pages or any(type(n) is not int or n < 0 for n in counts.values()):
        raise ValueError("Reviewed row counts must be nonnegative integers for input pages.")
    pages = extraction.get("pages")
    if not isinstance(pages, dict):
        raise ValueError("Extraction must contain pages.")
    pages = {page_number(key): rows for key, rows in pages.items()}
    for rows in pages.values():
        if not isinstance(rows, list):
            raise ValueError("Each extracted page must be a row list.")
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(ALL_FIELDS):
                raise ValueError("Every extracted row must contain all 13 fields.")
            for field, value in row.items():
                canonical(field, value)

    count_checks = [{"page": number, "expected": count,
                     "actual": len(pages[number]) if number in pages else None,
                     "passed": number in pages and len(pages[number]) == count}
                    for number, count in sorted(counts.items())]
    by_field = {field: {"checked": 0, "passed": 0, "failed": 0} for field in ALL_FIELDS}
    differences, checked = [], set()
    checks = review.get("checks")
    if not isinstance(checks, list):
        raise ValueError("Review must supply a list of cell checks.")
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Each check must specify page, row and expected fields.")
        number, index, fields = check.get("page"), check.get("row"), check.get("expected")
        if (type(number) is not int or number not in counts or type(index) is not int
                or not 1 <= index <= counts[number] or not isinstance(fields, dict)
                or not fields or not set(fields) <= set(ALL_FIELDS)):
            raise ValueError("Cell checks must address reviewed rows and known fields.")
        rows = pages.get(number, [])
        exists = index <= len(rows)
        for field, expected in fields.items():
            key = (number, index, field)
            if key in checked:
                raise ValueError("Duplicate cell checks could inflate review coverage.")
            checked.add(key)
            expected_value = canonical(field, expected)
            observed = rows[index - 1][field] if exists else None
            passed = exists and canonical(field, observed) == expected_value
            by_field[field]["checked"] += 1
            by_field[field]["passed" if passed else "failed"] += 1
            if not passed:
                differences.append({"page": number, "row": index, "field": field,
                                    "expected": expected, "actual": observed, "row_missing": not exists})
    counts_complete = set(counts) == expected_pages
    required = sum(counts.values()) * len(ALL_FIELDS) if counts_complete else None
    full_review = counts_complete and len(checked) == required
    page_coverage = set(pages) == expected_pages
    all_correct = (full_review and page_coverage and all(item["passed"] for item in count_checks)
                   and not differences)
    return {"all_correct": all_correct, "full_reference_reviewed": full_review,
            "pdf_sha256": pdf_sha256, "page_coverage_matches": page_coverage,
            "missing_pages": sorted(expected_pages - set(pages)),
            "extra_pages": sorted(set(pages) - expected_pages),
            "row_count_checks": count_checks, "fields_reviewed": len(checked),
            "fields_required": required,
            "unreviewed_fields": required - len(checked) if required is not None else None,
            "by_field": by_field, "differences": differences,
            "source_corrections": review.get("source_corrections", []),
            "scope": "Source-order cell checks; unreviewed fields are not certified."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--extraction", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        pdf_hash = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
        page_count = len(PdfReader(args.pdf).pages)
        report = audit(json.loads(args.extraction.read_text()), json.loads(args.review.read_text()),
                       pdf_hash, page_count)
        save_private(args.output, report)
    except (OSError, ValueError, TypeError):
        print("Accuracy audit failed to load: check PDF, matching hashes and review schema.", file=sys.stderr)
        return 1
    # Keep names and extracted document contents out of terminal logs.
    print(json.dumps({key: report[key] for key in (
        "all_correct", "full_reference_reviewed", "fields_reviewed", "fields_required", "by_field",
    )}, indent=2))
    return 0 if report["all_correct"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
