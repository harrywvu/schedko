import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncpg
import httpx
from fastapi import HTTPException, status
from pdf2image import convert_from_bytes

from config import (
    EXTRACTION_PROMPT, GEMINI_API_KEY, GEMINI_URL, MAX_ATTEMPTS_TO_RETRY,
    GEMINI_PDF_MODEL, GEMINI_RPM, GEMINI_INPUT_TPM, GEMINI_RPD,
    PDF_PROCESSING_MODE, PDF_EXTRACTION_STRATEGY, PDF_PIPELINE_VERSION,
)
from db import (
    _delete_file_artifacts, _set_file_state, get_pool,
    load_extraction_pages, save_extraction_pages,
)
from pdf_extraction import (
    ALL_FIELDS, ROW_FIELDS, SECTION_FIELDS, ExtractionError, InvalidExtraction,
    PdfExtractor, RequestLimiter, pdf_page_count, retry_details, validate_pages,
)
from parsing import normalize_schedule_row
from state import BACKGROUND_TASKS, PROCESSING_TASKS


def start_processing_job(file_hash: str, contents: bytes):
    task = asyncio.create_task(process_pdf_job(file_hash, contents))
    BACKGROUND_TASKS.add(task)
    PROCESSING_TASKS[file_hash] = task
    task.add_done_callback(BACKGROUND_TASKS.discard)

    def release(completed):
        if PROCESSING_TASKS.get(file_hash) is completed:
            PROCESSING_TASKS.pop(file_hash, None)

    task.add_done_callback(release)
    return task


_LIMITER = None
_LIMITER_LOOP = None
logger = logging.getLogger(__name__)


def get_request_limiter():
    global _LIMITER, _LIMITER_LOOP
    loop = asyncio.get_running_loop()
    if _LIMITER is None or _LIMITER_LOOP is not loop:
        _LIMITER = RequestLimiter(GEMINI_RPM, GEMINI_INPUT_TPM, GEMINI_RPD, reset_daily=True)
        _LIMITER_LOOP = loop
    return _LIMITER


def reusable_pages(saved, total):
    valid = {}
    for number, rows in saved.items():
        if type(number) is not int or not 1 <= number <= total or not isinstance(rows, list):
            continue
        if any(not isinstance(row, dict) or set(row) != set(ALL_FIELDS) for row in rows):
            continue
        compact = {"page": number, "kind": "schedule" if rows else "no_schedule", "sections": [
            {**{key: row[key] for key in SECTION_FIELDS},
             "rows": [[row[key] for key in ROW_FIELDS]]} for row in rows
        ]}
        try:
            valid.update(validate_pages({"pages": [compact]}, [number]))
        except InvalidExtraction:
            continue
    return valid


async def process_pdf_job(file_hash: str, contents: bytes):
    if PDF_PROCESSING_MODE == "images":
        return await _process_image_pdf_job(file_hash, contents)
    started = time.monotonic()
    try:
        if PDF_PROCESSING_MODE != "pdf":
            raise ExtractionError("Invalid PDF processing configuration.")
        await _set_file_state(file_hash, status_value="processing", stage="preparing",
                              retry_after_seconds=0, error=None)
        total = await asyncio.to_thread(pdf_page_count, contents)
        pages = reusable_pages(await load_extraction_pages(file_hash, PDF_PIPELINE_VERSION), total)
        await _set_file_state(file_hash, pages_total=total, pages_done=len(pages), pages_reused=len(pages))
        progress_lock = asyncio.Lock()

        async def checkpoint(extracted):
            # Serialize checkpoint + counter updates so progress cannot move backwards.
            async with progress_lock:
                await save_extraction_pages(file_hash, PDF_PIPELINE_VERSION, extracted)
                pages.update(extracted)
                await _set_file_state(file_hash, pages_total=total, pages_done=len(pages))

        async def update_stage(stage, retry_after_seconds=0):
            await _set_file_state(file_hash, stage=stage, retry_after_seconds=retry_after_seconds)

        if len(pages) < total:
            if not GEMINI_API_KEY:
                raise ExtractionError("The server's Gemini API key is not configured.")
            await update_stage("uploading")
            async with httpx.AsyncClient(timeout=150) as client:
                extractor = PdfExtractor(client, GEMINI_API_KEY, get_request_limiter(),
                                         model=GEMINI_PDF_MODEL, on_status=update_stage)
                async with extractor.uploaded_pdf(contents) as uri:
                    pages = await extractor.extract(uri, total, PDF_EXTRACTION_STRATEGY,
                                                    existing_pages=dict(pages), on_pages=checkpoint)
        if set(pages) != set(range(1, total + 1)):
            raise InvalidExtraction("Extraction did not cover every page.")
        rows = [row for number in sorted(pages) for row in pages[number]]
        if not rows:
            raise InvalidExtraction("No exam schedule rows were found in this PDF.")
        await update_stage("saving")
        await persist_rows(file_hash, rows, total_pages=total)
        logger.info("PDF processing completed: pages=%d rows=%d seconds=%.2f", total, len(rows), time.monotonic() - started)
    except asyncio.CancelledError:
        # The cancel endpoint deletes artifacts after awaiting this task.
        # Other cancellation (e.g. graceful shutdown) must retain resumable pages.
        try:
            await _set_file_state(file_hash, status_value="failed", stage="failed",
                                  retry_after_seconds=0,
                                  error="Processing was interrupted. Re-upload the same PDF to resume saved pages.")
        except Exception:
            logger.warning("Could not record interrupted PDF status; saved pages were retained.")
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, ExtractionError) else "PDF processing failed. Re-upload the same PDF to resume saved pages."
        logger.warning("PDF processing failed: %s", type(exc).__name__)
        await _set_file_state(file_hash, status_value="failed", stage="failed",
                              retry_after_seconds=0, error=message)


async def _process_image_pdf_job(file_hash: str, contents: bytes):
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
                pages_reused=0,
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
            stage="complete",
        )
    except asyncio.CancelledError:
        await _delete_file_artifacts(file_hash)
        raise
    except Exception:
        await _set_file_state(
            file_hash,
            status_value="failed",
            error="Image extraction failed. Please retry processing the PDF.",
            stage="failed",
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
            if response.is_error:
                wait, daily = retry_details(response)
                if daily or response.status_code not in (408, 429, 500, 502, 503, 504):
                    raise ExtractionError("Gemini rejected image extraction.")
                if attempt < MAX_ATTEMPTS_TO_RETRY - 1:
                    await asyncio.sleep(max(wait, 15))
                continue
            data = response.json()

            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)

        except ExtractionError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            if attempt < MAX_ATTEMPTS_TO_RETRY - 1:
                await asyncio.sleep(15)

    raise ExtractionError("Image extraction failed after three attempts.")


async def persist_rows(file_hash, all_rows, *, total_pages=None):
    if not isinstance(all_rows, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid rows format.")
    try:
        pool = await get_pool()
        async with pool.acquire() as db:
            async with db.transaction():
                if total_pages is not None:
                    file = await db.fetchrow("SELECT status FROM files WHERE hash = $1 FOR UPDATE", file_hash)
                    if not file or file["status"] != "processing":
                        raise asyncio.CancelledError()
                await db.execute("DELETE FROM schedules WHERE file_hash = $1", file_hash)
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
                if total_pages is not None:
                    await db.execute(
                        """UPDATE files SET status = 'ready', stage = 'complete',
                           pages_total = $2, pages_done = $2, error = NULL,
                           retry_after_seconds = 0, processed_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP WHERE hash = $1""",
                        file_hash, total_pages,
                    )
    except asyncpg.PostgresError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DB error: {exc}")
