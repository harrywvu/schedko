import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

ORIGIN = "https://schedko.example"

with patch.object(config, "CORS_ORIGINS", ORIGIN):
    import main

import routes


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = MagicMock()
        self.connection.fetchrow = AsyncMock(return_value=None)
        self.connection.execute = AsyncMock()
        self.pool = MagicMock()
        self.pool.acquire.return_value.__aenter__.return_value = self.connection
        self.pool_patch = patch.object(routes, "get_pool", AsyncMock(return_value=self.pool))
        self.pool_patch.start()
        self.addCleanup(self.pool_patch.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
            base_url="http://testserver",
            headers={"Origin": ORIGIN},
        )
        self.addAsyncCleanup(self.client.aclose)

    async def upload(self, contents=b"%PDF-1.4 synthetic test file"):
        return await self.client.post(
            "/upload", files={"file": ("schedule.pdf", contents, "application/pdf")}
        )

    def assert_cors(self, response):
        self.assertEqual(response.headers.get("access-control-allow-origin"), ORIGIN)
        self.assertEqual(response.headers.get("access-control-allow-credentials"), "true")

    async def test_new_upload_returns_hash_and_cors_headers(self):
        contents = b"%PDF-1.4 synthetic test file"
        response = await self.upload(contents)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "new")
        self.assertEqual(response.json()["hash"], hashlib.sha256(contents).hexdigest())
        self.assert_cors(response)
        self.assertIn("Retry-After", response.headers.get("access-control-expose-headers", ""))
        self.connection.execute.assert_awaited_once()

    async def test_cached_upload_does_not_reprocess(self):
        self.connection.fetchrow.side_effect = [
            {"status": "ready", "schema_version": config.EXTRACTION_SCHEMA_VERSION},
            {"exists": 1},
        ]
        response = await self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cached")
        self.assert_cors(response)
        self.connection.execute.assert_not_awaited()

    async def test_active_upload_keeps_processing_response(self):
        self.connection.fetchrow.side_effect = [
            {"status": "processing", "pages_total": 5, "pages_done": 2},
            None,
        ]
        with patch.object(routes, "_has_active_processing_task", return_value=True):
            response = await self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processing")
        self.assertEqual(response.json()["pages_done"], 2)
        self.assert_cors(response)
        self.connection.execute.assert_not_awaited()

    async def test_empty_upload_retains_validation_and_cors(self):
        response = await self.upload(b"")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Empty file.")
        self.assert_cors(response)

    async def test_database_error_response_keeps_cors_headers(self):
        self.connection.fetchrow.side_effect = RuntimeError("Database unavailable")
        response = await self.upload()
        self.assertEqual(response.status_code, 500)
        self.assert_cors(response)
        self.assertNotIn("Database unavailable", response.text)

    async def test_allowed_upload_preflight(self):
        response = await self.client.options(
            "/upload",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assert_cors(response)

    async def test_unlisted_origin_remains_blocked(self):
        response = await self.client.options(
            "/upload",
            headers={
                "Origin": "https://unlisted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    async def test_interrupted_job_reports_failure_instead_of_processing_forever(self):
        self.connection.fetchrow.return_value = {
            "status": "processing", "pages_total": 5, "pages_done": 2,
            "processing_started_at": None,
        }
        with patch.object(routes, "_has_active_processing_task", return_value=False):
            response = await self.client.get("/processing/test-hash")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertIn("interrupted", response.json()["message"])
        self.connection.execute.assert_awaited_once()
        self.assert_cors(response)

    async def test_active_job_preserves_progress(self):
        self.connection.fetchrow.return_value = {
            "status": "processing", "pages_total": 5, "pages_done": 2,
            "processing_started_at": None,
        }
        with patch.object(routes, "_has_active_processing_task", return_value=True):
            response = await self.client.get("/processing/test-hash")
        self.assertEqual(response.json()["status"], "processing")
        self.assertEqual(response.json()["progress_percent"], 40)
        self.connection.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
