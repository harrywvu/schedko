import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import processing


def sample_pdf():
    with Image.new("RGB", (595, 842), "white") as page, BytesIO() as buffer:
        page.save(buffer, format="PDF", save_all=True, append_images=[page, page], resolution=72)
        return buffer.getvalue()


@unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("pdfinfo"), "Poppler is required")
class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = AsyncMock()
        self.persist = AsyncMock()
        self.cleanup = AsyncMock()
        for name, replacement in [
            ("_set_file_state", self.state),
            ("persist_rows", self.persist),
            ("_delete_file_artifacts", self.cleanup),
        ]:
            patcher = patch.object(processing, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_real_pdf_uses_jpeg_files_and_releases_each_processed_page(self):
        processed_paths = []

        async def extract(_client, page_path):
            page = Path(page_path)
            self.assertTrue(page.is_file())
            self.assertTrue(page.read_bytes().startswith(b"\xff\xd8"))
            self.assertTrue(all(not previous.exists() for previous in processed_paths))
            processed_paths.append(page)
            return [{"subject": f"Exam {len(processed_paths)}"}]

        with (
            patch.object(processing, "extract_page", side_effect=extract),
            patch.object(processing.asyncio, "sleep", new=AsyncMock()),
        ):
            await processing.process_pdf_job("test-hash", sample_pdf())

        self.assertEqual(len(processed_paths), 3)
        self.assertFalse(processed_paths[0].parent.exists())
        self.assertEqual(len(self.persist.await_args.args[1]), 3)
        self.assertEqual(self.state.await_args.kwargs["status_value"], "ready")
        self.assertEqual(self.state.await_args.kwargs["pages_done"], 3)

    async def test_extraction_failure_removes_temporary_pages_and_marks_failed(self):
        paths = []

        async def fail(_client, page_path):
            paths.append(Path(page_path))
            raise RuntimeError("Extraction failed")

        with patch.object(processing, "extract_page", side_effect=fail):
            await processing.process_pdf_job("test-hash", sample_pdf())

        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].parent.exists())
        self.persist.assert_not_awaited()
        self.assertEqual(self.state.await_args.kwargs["status_value"], "failed")

    async def test_cancellation_removes_temporary_pages_and_database_artifacts(self):
        paths = []

        async def cancel(_client, page_path):
            paths.append(Path(page_path))
            raise asyncio.CancelledError()

        with patch.object(processing, "extract_page", side_effect=cancel):
            with self.assertRaises(asyncio.CancelledError):
                await processing.process_pdf_job("test-hash", sample_pdf())

        self.assertFalse(paths[0].parent.exists())
        self.cleanup.assert_awaited_once_with("test-hash")
        self.persist.assert_not_awaited()

    async def test_cancellation_waits_for_renderer_before_removing_its_directory(self):
        started = asyncio.Event()
        finish = threading.Event()
        loop = asyncio.get_running_loop()
        paths = []

        def render(_contents, **options):
            page = Path(options["output_folder"]) / "page.jpg"
            page.write_bytes(b"synthetic JPEG")
            paths.append(page)
            loop.call_soon_threadsafe(started.set)
            finish.wait(timeout=5)
            return [str(page)]

        with patch.object(processing, "convert_from_bytes", side_effect=render):
            task = asyncio.create_task(processing.process_pdf_job("test-hash", b"PDF"))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertTrue(paths[0].parent.exists())
            finally:
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        self.assertFalse(paths[0].parent.exists())
        self.cleanup.assert_awaited_once_with("test-hash")

    async def test_extract_page_sends_existing_jpeg_without_decoding_it(self):
        from tempfile import TemporaryDirectory

        def respond(request):
            payload = json.loads(request.content)
            image_data = payload["contents"][0]["parts"][1]["inline_data"]
            self.assertEqual(image_data["mime_type"], "image/jpeg")
            self.assertEqual(base64.b64decode(image_data["data"]), b"compressed JPEG bytes")
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": '[{"subject": "Exam"}]'}]}}],
            })

        with TemporaryDirectory() as folder:
            page = Path(folder) / "page.jpg"
            page.write_bytes(b"compressed JPEG bytes")
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with patch.object(processing, "GEMINI_URL", "https://gemini.example/extract"):
                    rows = await processing.extract_page(client, page)
        self.assertEqual(rows, [{"subject": "Exam"}])


if __name__ == "__main__":
    unittest.main()
