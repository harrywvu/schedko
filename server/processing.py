import asyncio
import base64
import json
from io import BytesIO

import aiosqlite
import httpx
from fastapi import HTTPException, status
from pdf2image import convert_from_bytes

from config import DB_PATH, EXTRACTION_PROMPT, GEMINI_URL, MAX_ATTEMPTS_TO_RETRY
from db import _delete_file_artifacts, _set_file_state
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
        images = await asyncio.to_thread(convert_from_bytes, contents, dpi=200)
        if not images:
            raise RuntimeError("No pages were extracted from the PDF.")

        await _set_file_state(
            file_hash,
            status_value="processing",
            pages_total=len(images),
            pages_done=0,
            error=None,
        )

        all_rows = await extract_all_pages(file_hash, images)
        if not isinstance(all_rows, list):
            raise RuntimeError("Extraction failed.")

        print(f"Extracted {len(all_rows)} rows total")

        await persist_rows(file_hash, all_rows)
        await _set_file_state(
            file_hash,
            status_value="ready",
            pages_total=len(images),
            pages_done=len(images),
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


async def extract_all_pages(file_hash: str, images) -> list:
    all_rows = []
    total_pages = len(images)
    async with httpx.AsyncClient() as client:
        for i, image in enumerate(images):
            print(f"Processing page {i + 1}/{total_pages}")
            await _set_file_state(
                file_hash,
                status_value="processing",
                pages_total=total_pages,
                pages_done=i,
            )
            rows = await extract_page(client, image)
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


async def extract_page(client: httpx.AsyncClient, image) -> list:
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    b64 = base64.b64encode(buffer.getvalue()).decode()

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
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows_to_insert = []
            for row in all_rows:
                if not isinstance(row, dict):
                    continue

                schedule_row = normalize_schedule_row({**row, "file_hash": file_hash})
                rows_to_insert.append((
                    schedule_row["file_hash"],
                    schedule_row["subject"],
                    schedule_row["class_time"],
                    schedule_row["class_day"],
                    schedule_row["class_days"],
                    schedule_row["exam_time"],
                    schedule_row["exam_day"],
                    schedule_row["course_year"],
                    schedule_row["num_students"],
                    schedule_row["instructor"],
                    schedule_row["examiner"],
                    schedule_row["exam_room"],
                    schedule_row["exam_building"],
                    schedule_row["major_exam"],
                    schedule_row["semester"],
                    schedule_row["academic_year"],
                    schedule_row["day"],
                    schedule_row["room"],
                    schedule_row["building"],
                ))

            await db.executemany(
                """
                INSERT INTO schedules (
                    file_hash, subject, class_time, class_day, class_days,
                    exam_time, exam_day, course_year, num_students,
                    instructor, examiner, exam_room, exam_building,
                    major_exam, semester, academic_year, day, room, building
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_to_insert,
            )
            await db.commit()
    except aiosqlite.Error as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DB error: {exc}")
