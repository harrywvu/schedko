import asyncio
from contextlib import asynccontextmanager
import copy
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import processing
from pdf_extraction import ExtractionError, RequestLimiter, pdf_page_count, validate_pages


def sample_pdf(count=4):
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=595, height=842)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def compact_page(number):
    return {"page": number, "kind": "schedule", "sections": [{
        "exam_day": "May 7, 2026", "exam_time": "9:00-10:00 AM", "major_exam": "Finals",
        "semester": "Second", "academic_year": "2025-2026",
        "rows": [[f"TEST {number}", "8:00-9:00", "MWF", "TEST 1-A", "Instructor",
                  "Examiner", "201", "Building"]],
    }]}


def completion(first, last):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": json.dumps({"pages": [compact_page(i) for i in range(first, last + 1)]})},
    ]}}]}


class ProcessingV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}
        self.state = {}
        self.generations = []
        self.uploads = 0
        self.deletions = 0
        self.failure = None
        self.now = 0
        self.contents = sample_pdf()

        async def sleep(seconds):
            self.now += seconds

        limiter = RequestLimiter(5, 1000, 20, clock=lambda: self.now, sleep=sleep)

        async def set_state(_hash, **values):
            self.state.update(values)

        async def load_pages(_hash, _version):
            return copy.deepcopy(self.saved)

        async def save_pages(_hash, _version, pages):
            self.saved.update(copy.deepcopy(pages))

        async def publish(_hash, _rows, *, total_pages):
            self.assertTrue(set(range(1, total_pages + 1)).issubset(self.saved))
            self.state.update(status_value="ready", stage="complete", pages_done=total_pages)

        async def cleanup(_hash):
            self.saved.clear()

        self.persist = AsyncMock(side_effect=publish)
        self.set_state = AsyncMock(side_effect=set_state)
        self.save_pages = AsyncMock(side_effect=save_pages)
        self.cleanup = AsyncMock(side_effect=cleanup)
        client_type = httpx.AsyncClient

        def client(**kwargs):
            return client_type(transport=httpx.MockTransport(self.respond), **kwargs)

        for target, value in {
            "PDF_PROCESSING_MODE": "pdf", "PDF_EXTRACTION_STRATEGY": "split",
            "GEMINI_API_KEY": "test-key", "get_request_limiter": lambda: limiter,
            "_set_file_state": self.set_state, "load_extraction_pages": AsyncMock(side_effect=load_pages),
            "save_extraction_pages": self.save_pages, "persist_rows": self.persist,
            "_delete_file_artifacts": self.cleanup,
        }.items():
            patched = patch.object(processing, target, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.client_patch = patch.object(processing.httpx, "AsyncClient", side_effect=client)
        self.client_factory = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    async def respond(self, request):
        self.assertNotIn("test-key", str(request.url))
        if request.method == "DELETE":
            self.deletions += 1
            return httpx.Response(200, json={})
        if request.headers.get("X-Goog-Upload-Command") == "start":
            self.uploads += 1
            return httpx.Response(200, headers={"X-Goog-Upload-URL":
                "https://generativelanguage.googleapis.com/upload/session"})
        if request.headers.get("X-Goog-Upload-Command") == "upload, finalize":
            self.assertEqual(request.content, self.contents)
            return httpx.Response(200, json={"file": {
                "name": "files/test", "state": "ACTIVE", "uri": "file-uri",
            }})
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 100})
        data = json.loads(request.content)
        self.assertEqual(data["contents"][0]["parts"][0]["fileData"]["mimeType"], "application/pdf")
        prompt = data["contents"][0]["parts"][1]["text"]
        first, last = map(int, re.search(r"pages (\d+) through (\d+)", prompt).groups())
        self.generations.append((first, last, self.now))
        if self.failure and first >= 3:
            if self.failure == "cancel":
                raise asyncio.CancelledError()
            return httpx.Response(403, json={"error": {"message": "private provider details"}})
        return httpx.Response(200, json=completion(first, last))

    async def test_default_pipeline_uploads_pdf_once_extracts_halves_and_publishes(self):
        with patch.object(processing, "convert_from_bytes") as render:
            await processing.process_pdf_job("hash", self.contents)
        render.assert_not_called()
        self.assertEqual((self.uploads, self.deletions), (1, 1))
        self.assertEqual(self.generations, [(1, 2, 0), (3, 4, 13)])
        self.assertEqual(self.state["status_value"], "ready")
        self.assertEqual(self.state["pages_done"], 4)
        rows = self.persist.await_args.args[1]
        self.assertEqual([row["subject"] for row in rows], [f"TEST {i}" for i in range(1, 5)])
        self.assertEqual(rows[0]["exam_time"], "9:00-10:00 AM")
        self.assertEqual(self.persist.await_args.kwargs, {"total_pages": 4})
        progress = [call.kwargs["pages_done"] for call in self.set_state.await_args_list if "pages_done" in call.kwargs]
        self.assertEqual(progress, sorted(progress))

    async def test_failed_half_keeps_checkpoints_and_retry_only_extracts_missing_pages(self):
        self.failure = "permanent"
        await processing.process_pdf_job("hash", self.contents)
        self.assertEqual(self.state["status_value"], "failed")
        self.assertEqual(set(self.saved), {1, 2})
        self.persist.assert_not_awaited()
        self.assertNotIn("private provider details", self.state["error"])
        self.failure = None
        await processing.process_pdf_job("hash", self.contents)
        self.assertEqual([(first, last) for first, last, _ in self.generations], [(1, 2), (3, 4), (3, 4)])
        self.assertEqual(self.state["status_value"], "ready")
        self.persist.assert_awaited_once()
        self.assertEqual(self.deletions, 2)

    async def test_all_saved_pages_need_no_gemini_calls(self):
        self.saved = validate_pages({"pages": [compact_page(i) for i in range(1, 5)]}, range(1, 5))
        await processing.process_pdf_job("hash", self.contents)
        self.client_factory.assert_not_called()
        self.persist.assert_awaited_once()

    async def test_corrupt_and_out_of_range_checkpoints_are_reextracted(self):
        self.saved = {1: [{"subject": "incomplete"}], 5: []}
        await processing.process_pdf_job("hash", self.contents)
        self.assertEqual([(a, b) for a, b, _ in self.generations], [(1, 2), (3, 4)])
        self.assertEqual(self.state["status_value"], "ready")
        self.persist.assert_awaited_once()

    async def test_interruption_cleans_remote_upload_but_keeps_checkpoints_for_resume(self):
        self.failure = "cancel"
        with self.assertRaises(asyncio.CancelledError):
            await processing.process_pdf_job("hash", self.contents)
        self.cleanup.assert_not_awaited()
        self.assertEqual(set(self.saved), {1, 2})
        self.assertEqual(self.state["status_value"], "failed")
        self.assertEqual(self.deletions, 1)
        self.persist.assert_not_awaited()

    async def test_invalid_pdf_fails_before_network(self):
        await processing.process_pdf_job("hash", b"not a PDF")
        self.client_factory.assert_not_called()
        self.persist.assert_not_awaited()
        self.assertEqual(self.state["status_value"], "failed")

    async def test_missing_api_key_fails_without_network_or_publication(self):
        with patch.object(processing, "GEMINI_API_KEY", None):
            await processing.process_pdf_job("hash", self.contents)
        self.client_factory.assert_not_called()
        self.persist.assert_not_awaited()
        self.assertIn("not configured", self.state["error"])

    async def test_cover_only_pdf_is_not_published_as_empty_success(self):
        self.saved = {number: [] for number in range(1, 5)}
        await processing.process_pdf_job("hash", self.contents)
        self.persist.assert_not_awaited()
        self.assertEqual(self.state["status_value"], "failed")


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.db = MagicMock()
        self.db.fetchrow = AsyncMock(return_value={"status": "processing"})

        async def execute(sql, *args):
            self.events.append("ready" if "status = 'ready'" in sql else "delete")

        async def insert(_sql, rows):
            self.events.append("insert")
            self.inserted = rows

        @asynccontextmanager
        async def transaction():
            self.events.append("begin")
            try:
                yield
            except BaseException:
                self.events.append("rollback")
                raise
            else:
                self.events.append("commit")

        self.db.execute = AsyncMock(side_effect=execute)
        self.db.executemany = AsyncMock(side_effect=insert)
        self.db.transaction.side_effect = transaction
        self.pool = MagicMock()
        self.pool.acquire.return_value.__aenter__.return_value = self.db
        patched = patch.object(processing, "get_pool", AsyncMock(return_value=self.pool))
        patched.start()
        self.addCleanup(patched.stop)

    async def test_rows_and_ready_status_are_committed_together_with_deduplication(self):
        row = validate_pages({"pages": [compact_page(1)]}, [1])[1][0]
        await processing.persist_rows("hash", [row, row], total_pages=1)
        self.assertEqual(self.events, ["begin", "delete", "insert", "ready", "commit"])
        self.assertEqual(len(self.inserted), 1)

    async def test_insert_failure_rolls_back_and_never_marks_ready(self):
        self.db.executemany.side_effect = asyncpg.PostgresError("test constraint failure")
        with self.assertRaises(processing.HTTPException):
            await processing.persist_rows("hash", [], total_pages=1)
        self.assertEqual(self.events, ["begin", "delete", "rollback"])

    async def test_cancellation_prevents_publication(self):
        self.db.fetchrow.return_value = {"status": "cancelled"}
        with self.assertRaises(asyncio.CancelledError):
            await processing.persist_rows("hash", [], total_pages=1)
        self.db.executemany.assert_not_awaited()
        self.assertEqual(self.events, ["begin", "rollback"])


class PdfInputTests(unittest.TestCase):
    def test_page_count_and_encrypted_input(self):
        self.assertEqual(pdf_page_count(sample_pdf(3)), 3)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("test-password")
        stream = BytesIO()
        writer.write(stream)
        with self.assertRaisesRegex(ExtractionError, "Password-protected"):
            pdf_page_count(stream.getvalue())

    def test_unknown_exam_times_are_not_replaced_with_class_times(self):
        from parsing import normalize_schedule_row, rows_to_events
        row = normalize_schedule_row({"subject": "TEST", "exam_day": "May 7, 2026",
                                      "class_time": "8:00-9:00 AM", "exam_time": None})
        self.assertIsNone(row["exam_time"])
        self.assertEqual(rows_to_events([row]), [])

    def test_unknown_exam_day_is_not_replaced_with_recurring_class_days(self):
        from parsing import normalize_schedule_row, rows_to_events
        row = normalize_schedule_row({"subject": "TEST", "exam_day": None, "class_days": "MWF",
                                      "exam_time": "8:00-9:00 AM"})
        self.assertIsNone(row["exam_day"])
        self.assertEqual(rows_to_events([row]), [])


if __name__ == "__main__":
    unittest.main()
