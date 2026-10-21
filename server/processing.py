import asyncio
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncpg
import httpx
from fastapi import HTTPException, status
from pdf2image import convert_from_bytes

from config import EXTRACTION_PROMPT, GEMINI_URL, MAX_ATTEMPTS_TO_RETRY
from db import _delete_file_artifacts, _set_file_state, get_pool
from parsing import normalize_schedule_row
from state import BACKGROUND_TASKS, PROCESSING_TASKS


def start_processing_job(file_hash: str, contents: bytes):
    task = asyncio.create_task(process_pdf_job(file_hash, contents))
    BACKGROUND_TASKS.add(task)
    PROCESSING_TASKS[file_hash] = task
    task.add_done_callback(BACKGROUND_TASKS.discard)
    task.add_done_callback(lambda _task, key=file_hash: PROCESSING_TASKS.pop(key, None))
    return task


async def process_pdf_job(file_hash: str, contents: bytes):
    try:
        with TemporaryDirectory(prefix="schedko-") as output_folder:
            # Keep compressed pages on disk instead of retaining every raw
            # page in RAM. Poppler renders sequentially with one worker.
            render_task = asyncio.create_task(asyncio.to_thread(
                convert_from_bytes,
                contents,
                dpi=200,
                fmt="jpeg",
                output_folder=output_folder,
                paths_only=True,
                thread_count=1,
            ))
            try:
                page_paths = await asyncio.shield(render_task)
            except asyncio.CancelledError:
                # A worker thread cannot be cancelled. Let it finish before
                # deleting the directory it is still writing into.
                await asyncio.gather(render_task, return_exceptions=True)
                raise
            del contents
            total_pages = len(page_paths)
            if not total_pages:
                raise RuntimeError("No pages were extracted from the PDF.")

            await _set_file_state(
                file_hash,
                status_value="processing",
                pages_total=total_pages,
                pages_done=0,
                error=None,
            )

            all_rows = await extract_all_pages(file_hash, page_paths)
        if not isinstance(all_rows, list):
            raise RuntimeError("Extraction failed.")

        print(f"Extracted {len(all_rows)} rows total")

        await persist_rows(file_hash, all_rows)
        await _set_file_state(
            file_hash,
            status_value="ready",
            pages_total=total_pages,
            pages_done=total_pages,
            error=None,
            processed_at=True,
        )
    except asyncio.CancelledError:
        await _delete_file_artifacts(file_hash)
        raise
    except Exception as exc:
        await _set_file_state(
            file_hash,
            status_value="failed",
            error=str(exc),
        )


async def extract_all_pages(file_hash: str, page_paths) -> list:
    all_rows = []
    total_pages = len(page_paths)
    async with httpx.AsyncClient() as client:
        for i, page_path in enumerate(page_paths):
            print(f"Processing page {i + 1}/{total_pages}")
            await _set_file_state(
                file_hash,
                status_value="processing",
                pages_total=total_pages,
                pages_done=i,
            )
            try:
                rows = await extract_page(client, page_path)
            finally:
                Path(page_path).unlink(missing_ok=True)
            all_rows.extend(rows)
            await _set_file_state(
                file_hash,
                status_value="processing",
                pages_total=total_pages,
                pages_done=i + 1,
            )
            if i < total_pages - 1:
                await asyncio.sleep(13)  # stay under 5 RPM
    return all_rows


async def extract_page(client: httpx.AsyncClient, page_path) -> list:
    image_bytes = await asyncio.to_thread(Path(page_path).read_bytes)
    b64 = base64.b64encode(image_bytes).decode()
    del image_bytes

    payload = {
        "contents": [{
            "parts": [
                {"text": EXTRACTION_PROMPT},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            ]
        }]
    }

    for attempt in range(MAX_ATTEMPTS_TO_RETRY):
        try:
            response = await client.post(GEMINI_URL, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                wait = int(data["error"].get("details", [{}])[-1].get("retryDelay", "30s").replace("s", ""))
                print(f"Rate limited, retrying in {wait}s")
                await asyncio.sleep(wait)
                continue

            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)

        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(15)

    print("All retries failed for this page")
    return []


async def persist_rows(file_hash, all_rows):
    if not isinstance(all_rows, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid rows format.")
    try:
        pool = await get_pool()
        async with pool.acquire() as db:
            async with db.transaction():
                rows_to_insert = []
                seen_keys = set()
                for row in all_rows:
                    if not isinstance(row, dict):
                        continue

                    schedule_row = normalize_schedule_row({**row, "file_hash": file_hash})
                    dedupe_key = (
                        schedule_row["file_hash"],
                        schedule_row["subject"],
                        schedule_row["class_time"],
                        schedule_row["class_days"],
                        schedule_row["exam_day"],
                        schedule_row["exam_time"],
                        schedule_row["course_year"],
                        schedule_row["instructor"],
                        schedule_row["examiner"],
                        schedule_row["exam_room"],
                        schedule_row["exam_building"],
                        schedule_row["major_exam"],
                        schedule_row["semester"],
                        schedule_row["academic_year"],
                    )
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    rows_to_insert.append((
                        schedule_row["file_hash"],
                        schedule_row["subject"],
                        schedule_row["class_time"],
                        schedule_row["class_days"],
                        schedule_row["exam_time"],
                        schedule_row["exam_day"],
                        schedule_row["course_year"],
                        schedule_row["instructor"],
                        schedule_row["examiner"],
                        schedule_row["exam_room"],
                        schedule_row["exam_building"],
                        schedule_row["major_exam"],
                        schedule_row["semester"],
                        schedule_row["academic_year"],
                    ))

                await db.executemany(
                    """
                    INSERT INTO schedules (
                        file_hash, subject, class_time, class_days,
                        exam_time, exam_day, course_year,
                        instructor, examiner, exam_room, exam_building,
                        major_exam, semester, academic_year
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                    rows_to_insert,
                )
    except asyncpg.PostgresError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DB error: {exc}")
