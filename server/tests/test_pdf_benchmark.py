import asyncio
import copy
from datetime import date
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.benchmark_pdf import compare_pages, select_strategy
from benchmarks.pdf_extraction import (
    ALL_FIELDS, ExtractionError, InvalidExtraction, PdfExtractor,
    QuotaExhausted, RequestLimiter, retry_details, validate_pages,
)


def page(number=1):
    return {"page": number, "kind": "schedule", "sections": [{
        "exam_day": "May 7, 2026", "exam_time": "9:00–10:00 AM", "major_exam": "Finals",
        "semester": "Second", "academic_year": "2025–2026", "rows": [
            ["TEST 101", "8:00–9:00", "MWF", "TEST 1-A", "Instructor", "Examiner", "201", "Building"],
        ],
    }]}


def completion(*pages):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"thought": True, "text": "not part of the extraction"},
        {"text": json.dumps({"pages": list(pages)})},
    ]}}], "usageMetadata": {"totalTokenCount": 123}}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class ValidationTests(unittest.TestCase):
    def test_expands_metadata_and_preserves_duplicates_and_unknowns(self):
        item = page()
        item["sections"][0]["rows"][0][-1] = None
        item["sections"][0]["rows"] *= 2
        actual = validate_pages({"pages": [item]}, [1])[1]
        self.assertEqual(len(actual), 2)
        self.assertEqual(set(actual[0]), set(ALL_FIELDS))
        self.assertIsNone(actual[0]["exam_building"])
        self.assertEqual(actual[0]["exam_time"], "9:00–10:00 AM")

    def test_missing_duplicate_and_wrong_pages_fail(self):
        for pages in ([], [page(), page()], [page(2)]):
            with self.subTest(pages=pages), self.assertRaises(InvalidExtraction):
                validate_pages({"pages": pages}, [1])

    def test_blank_cover_is_explicit_and_schedule_cannot_be_empty(self):
        self.assertEqual(validate_pages({"pages": [
            {"page": 1, "kind": "no_schedule", "sections": []},
        ]}, [1]), {1: []})
        with self.assertRaises(InvalidExtraction):
            validate_pages({"pages": [{"page": 1, "kind": "schedule", "sections": []}]}, [1])

    def test_rejects_wrong_row_width_and_non_text_cells(self):
        for values in (["too", "short"], [1] * 8, [None] * 8):
            item = page()
            item["sections"][0]["rows"] = [values]
            with self.assertRaises(InvalidExtraction):
                validate_pages({"pages": [item]}, [1])

    def test_student_count_cannot_shift_into_instructor_column(self):
        item = page()
        item["sections"][0]["rows"][0][4] = "33"
        with self.assertRaises(InvalidExtraction):
            validate_pages({"pages": [item]}, [1])

    def test_exam_type_must_be_known_type_or_explicitly_unknown(self):
        for value in ("Finals", "Midterms", None):
            with self.subTest(value=value):
                item = page()
                item["sections"][0]["major_exam"] = value
                result = validate_pages({"pages": [item]}, [1])
                self.assertEqual(result[1][0]["major_exam"], value)

    def test_subject_and_placeholder_strings_cannot_become_exam_type(self):
        for value in ("TEST 101", "null", "", "Unknown"):
            with self.subTest(value=value):
                item = page()
                item["sections"][0]["major_exam"] = value
                with self.assertRaises(InvalidExtraction):
                    validate_pages({"pages": [item]}, [1])

    def test_ambiguous_cells_remain_null_without_dropping_or_shifting_row(self):
        item = page()
        item["sections"][0].update(exam_day=None, exam_time=None, major_exam=None)
        item["sections"][0]["rows"][0][3] = None
        item["sections"][0]["rows"][0][6] = None
        actual = validate_pages({"pages": [item]}, [1])[1]
        self.assertEqual(len(actual), 1)
        for field in ("exam_day", "exam_time", "major_exam", "course_year", "exam_room"):
            self.assertIsNone(actual[0][field])
        self.assertEqual(actual[0]["instructor"], "Instructor")
        self.assertEqual(actual[0]["exam_building"], "Building")

    def test_reference_compares_all_fields_per_page_and_counts_duplicates(self):
        reference = validate_pages({"pages": [page()]}, [1])
        actual = copy.deepcopy(reference)
        actual[1][0]["subject"] = " test   101 "
        self.assertTrue(compare_pages(actual, reference)["verified"])
        actual[1].append(copy.deepcopy(actual[1][0]))
        self.assertFalse(compare_pages(actual, reference)["verified"])
        actual = copy.deepcopy(reference)
        actual[1][0]["exam_room"] = "202"
        self.assertFalse(compare_pages(actual, reference)["verified"])
        self.assertFalse(compare_pages(actual, None)["verified"])

    def test_strategy_gate_requires_three_runs_accuracy_and_time(self):
        runs = [{"strategy": strategy, "success": True, "seconds": seconds,
                 "accuracy": {"verified": True}}
                for strategy, seconds in (("full", 105), ("split", 100)) for _ in range(3)]
        self.assertEqual(select_strategy(runs), "full")
        runs[0]["seconds"] = 181
        self.assertEqual(select_strategy(runs), "split")
        runs[-1]["accuracy"]["verified"] = False
        self.assertIsNone(select_strategy(runs))
        self.assertIsNone(select_strategy(runs[:2]))

    def test_retry_metadata_handles_decimal_duration_dates_and_daily_quota(self):
        response = httpx.Response(429, headers={"Retry-After": "5"}, json={"error": {"details": [
            {"retryDelay": "12.5s"},
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        ]}})
        self.assertEqual(retry_details(response), (12.5, True))
        response = httpx.Response(503, headers={"Retry-After": format_datetime(
            datetime.now(timezone.utc) + timedelta(seconds=30))})
        self.assertGreater(retry_details(response)[0], 28)


class LimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_budget_resets_on_pacific_calendar_day_change(self):
        day = date(2026, 10, 23)
        clock = FakeClock()
        limiter = RequestLimiter(5, 1000, 1, clock=clock, sleep=clock.sleep,
                                 reset_daily=True, today=lambda: day)
        await limiter.acquire(10)
        with self.assertRaises(QuotaExhausted):
            await limiter.acquire(10)
        day = date(2026, 10, 24)
        await limiter.acquire(10)
        self.assertEqual(limiter.remaining, 0)

    async def test_waits_between_starts_but_not_after_a_long_response(self):
        clock = FakeClock()
        limiter = RequestLimiter(5, 1000, 3, clock=clock, sleep=clock.sleep)
        await limiter.acquire(100)
        await limiter.acquire(100)
        self.assertEqual(clock.waits, [13])
        clock.now += 20
        await limiter.acquire(100)
        self.assertEqual(clock.waits, [13])

    async def test_token_window_and_daily_budget_include_each_attempt(self):
        clock = FakeClock()
        limiter = RequestLimiter(5, 100, 2, clock=clock, sleep=clock.sleep)
        await limiter.acquire(60)
        await limiter.acquire(60)
        self.assertEqual(clock.now, 60)
        with self.assertRaises(QuotaExhausted):
            await limiter.acquire(1)

    async def test_server_cooldown_applies_to_other_pending_requests(self):
        clock = FakeClock()
        limiter = RequestLimiter(5, 1000, 2, clock=clock, sleep=clock.sleep)
        await limiter.acquire(100)
        limiter.defer(40)
        await limiter.acquire(100)
        self.assertEqual(clock.now, 40)


class ExtractorTests(unittest.IsolatedAsyncioTestCase):
    def extractor(self, client):
        clock = FakeClock()
        limiter = RequestLimiter(5, 1000, 10, clock=clock, sleep=clock.sleep)
        return PdfExtractor(client, "test-key", limiter), clock

    async def test_split_requests_overlap_but_start_thirteen_seconds_apart(self):
        started = []
        both_started = asyncio.Event()
        clock = FakeClock()

        async def respond(request):
            if request.url.path.endswith(":countTokens"):
                return httpx.Response(200, json={"totalTokens": 100})
            number = len(started) + 1
            started.append(clock.now)
            if number == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return httpx.Response(200, json=completion(page(number)))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            limiter = RequestLimiter(5, 1000, 10, clock=clock, sleep=clock.sleep)
            extractor = PdfExtractor(client, "test-key", limiter)
            result = await extractor.extract("uri", 2, "split")
            self.assertEqual(started, [0, 13])
            self.assertEqual(list(result), [1, 2])

    async def test_three_network_failures_raise_instead_of_returning_empty_rows(self):
        attempts = []

        def respond(request):
            if request.url.path.endswith(":countTokens"):
                return httpx.Response(200, json={"totalTokens": 100})
            attempts.append(1)
            raise httpx.ReadTimeout("test timeout")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            extractor, _ = self.extractor(client)
            with self.assertRaisesRegex(ExtractionError, "three attempts"):
                await extractor.extract_range("uri", 1, 1)
            self.assertEqual(len(attempts), 3)

    async def test_429_retries_through_shared_limiter_and_filters_thoughts(self):
        attempts = 0

        def respond(request):
            nonlocal attempts
            if request.url.path.endswith(":countTokens"):
                return httpx.Response(200, json={"totalTokens": 100})
            attempts += 1
            self.assertNotIn("test-key", str(request.url))
            if attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "40"}, json={"error": {}})
            return httpx.Response(200, json=completion(page()))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            extractor, clock = self.extractor(client)
            result = await extractor.extract_range("file-uri", 1, 1)
            self.assertEqual(len(result[1]), 1)
            self.assertEqual(attempts, 2)
            self.assertGreaterEqual(clock.now, 40)
            self.assertEqual(extractor.limiter.remaining, 8)

    async def test_authentication_and_daily_quota_are_not_retried(self):
        for status in (403, 429):
            attempts = []

            def respond(request):
                if request.url.path.endswith(":countTokens"):
                    return httpx.Response(200, json={"totalTokens": 100})
                attempts.append(1)
                return httpx.Response(status, json={"error": {"details": [
                    {"violations": [{"quotaId": "RequestsPerDay"}]},
                ]}})

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                extractor, _ = self.extractor(client)
                with self.assertRaises(ExtractionError):
                    await extractor.extract_range("file-uri", 1, 1)
                self.assertEqual(len(attempts), 1)

    async def test_truncated_output_is_split_and_reassembled_in_order(self):
        calls = []
        async with httpx.AsyncClient() as client:
            extractor, _ = self.extractor(client)

            async def extract(_uri, first, last):
                calls.append((first, last))
                if first != last:
                    raise InvalidExtraction("truncated")
                return {first: [{"subject": str(first)}]}

            with patch.object(extractor, "extract_range", side_effect=extract):
                result = await extractor.extract("uri", 4, "split")
            self.assertEqual(list(result), [1, 2, 3, 4])
            self.assertEqual(len(calls), 6)

    async def test_failure_cancels_other_request_before_returning(self):
        cancelled = asyncio.Event()
        both_started = asyncio.Event()
        async with httpx.AsyncClient() as client:
            extractor, _ = self.extractor(client)

            async def extract(_uri, first, _last):
                if first == 1:
                    await both_started.wait()
                    raise ExtractionError("failed")
                both_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            with patch.object(extractor, "extract_range", side_effect=extract):
                with self.assertRaises(ExtractionError):
                    await extractor.extract("uri", 4, "split")
            self.assertTrue(cancelled.is_set())

    async def test_fragmented_saved_pages_only_extract_missing_ranges(self):
        extracted = []
        notified = []
        async with httpx.AsyncClient() as client:
            extractor, _ = self.extractor(client)

            async def extract(_uri, first, last):
                extracted.append((first, last))
                return {number: [] for number in range(first, last + 1)}

            async def checkpoint(pages):
                notified.append(set(pages))

            with patch.object(extractor, "extract_range", side_effect=extract):
                result = await extractor.extract("uri", 6, "split", existing_pages={1: [], 3: [], 5: []},
                                                 on_pages=checkpoint)
            self.assertEqual(extracted, [(2, 2), (4, 4), (6, 6)])
            self.assertEqual(notified, [{2}, {4}, {6}])
            self.assertEqual(list(result), list(range(1, 7)))

    async def test_upload_is_deleted_on_success_failure_and_cancellation(self):
        for outcome in ("success", "failure", "cancel"):
            methods = []

            def respond(request):
                methods.append(request.method)
                if request.headers.get("X-Goog-Upload-Command") == "start":
                    return httpx.Response(200, headers={"X-Goog-Upload-URL":
                        "https://generativelanguage.googleapis.com/upload/session"})
                if request.method == "DELETE":
                    return httpx.Response(200, json={})
                return httpx.Response(200, json={"file": {
                    "name": "files/test", "uri": "file-uri", "state": "ACTIVE",
                }})

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                extractor, _ = self.extractor(client)
                try:
                    async with extractor.uploaded_pdf(b"test-pdf") as uri:
                        self.assertEqual(uri, "file-uri")
                        if outcome == "failure":
                            raise ExtractionError("failed")
                        if outcome == "cancel":
                            raise asyncio.CancelledError
                except (ExtractionError, asyncio.CancelledError):
                    pass
            self.assertEqual(methods, ["POST", "POST", "DELETE"])


if __name__ == "__main__":
    unittest.main()
