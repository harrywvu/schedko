import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
from state import BACKGROUND_TASKS


class CheckpointStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = MagicMock()
        self.connection.fetch = AsyncMock(return_value=[])
        self.connection.execute = AsyncMock()
        self.connection.executemany = AsyncMock()
        self.pool = MagicMock()
        self.pool.acquire.return_value.__aenter__.return_value = self.connection
        patched = patch.object(db, "_POOL", self.pool)
        patched.start()
        self.addCleanup(patched.stop)

    async def test_checkpoint_codec_round_trip_includes_empty_cover_pages_and_version(self):
        pages = {1: [], 2: [{"subject": "TEST", "exam_room": None}]}
        await db.save_extraction_pages("hash", "pdf-v2:model:1", pages)
        batch = self.connection.executemany.await_args.args[1]
        self.assertEqual([row[:3] for row in batch], [("hash", "pdf-v2:model:1", 1),
                                                     ("hash", "pdf-v2:model:1", 2)])
        self.connection.fetch.return_value = [
            {"page_number": number, "rows": encoded} for _, _, number, encoded in batch
        ]
        self.assertEqual(await db.load_extraction_pages("hash", "pdf-v2:model:1"), pages)
        self.assertEqual(self.connection.fetch.await_args.args[1:], ("hash", "pdf-v2:model:1"))
        self.assertIsNone(json.loads(batch[1][3])[0]["exam_room"])

    async def test_error_can_be_explicitly_cleared_without_clearing_it_on_every_progress_update(self):
        await db._set_file_state("hash", error=None, stage="preparing", retry_after_seconds=0)
        sql, *params = self.connection.execute.await_args.args
        self.assertIn("error =", sql)
        self.assertIn(None, params)
        await db._set_file_state("hash", pages_done=3)
        self.assertNotIn("error =", self.connection.execute.await_args.args[0])

    async def test_resumed_pages_count_as_progress_but_not_newly_timed_work(self):
        row = {"status": "processing", "pages_total": 20, "pages_done": 10,
               "pages_reused": 10, "processing_started_at": datetime(2026, 10, 23)}
        payload = db._processing_payload("Resuming", row)
        self.assertEqual(payload["progress_percent"], 50)
        self.assertIsNone(payload["estimated_remaining_seconds"])


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_waits_for_jobs_before_closing_database(self):
        import main
        events = []
        started = asyncio.Event()

        async def job():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("checkpoint-preserved")

        task = asyncio.create_task(job())
        await started.wait()
        BACKGROUND_TASKS.add(task)
        try:
            async def close():
                self.assertTrue(task.done())
                events.append("database-closed")

            with patch.object(main, "close_db", AsyncMock(side_effect=close)):
                await main.shutdown()
        finally:
            BACKGROUND_TASKS.discard(task)
        self.assertEqual(events, ["checkpoint-preserved", "database-closed"])


if __name__ == "__main__":
    unittest.main()
