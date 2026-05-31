import hashlib

import aiosqlite
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from config import DB_PATH, EXTRACTION_SCHEMA_VERSION, ESTIMATED_PROCESSING_MINUTES
from db import (
    _delete_file_artifacts,
    _fetch_file_row,
    _has_active_processing_task,
    _has_schedule_rows,
    _processing_payload,
)
from parsing import normalize_lookup_value, normalize_schedule_row, rows_to_events
from processing import start_processing_job
from schemas import ScheduleRequest

router = APIRouter()


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    file_hash = hashlib.sha256(contents).hexdigest()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing = await _fetch_file_row(db, file_hash)
        has_rows = await _has_schedule_rows(db, file_hash) if existing else False

        if existing and existing["status"] == "processing":
            if not _has_active_processing_task(file_hash):
                await _delete_file_artifacts(file_hash, db)
                existing = None
                has_rows = False
            else:
                return {
                    "hash": file_hash,
                    "status": "processing",
                    "message": "This pdf is already being processed. Please wait",
                    "pages_total": existing["pages_total"],
                    "pages_done": existing["pages_done"] or 0,
                }

        if existing and int(existing["schema_version"] or 1) == EXTRACTION_SCHEMA_VERSION and has_rows and existing["status"] == "ready":
            return {"hash": file_hash, "status": "cached", "message": "This exam schedule is already in the database."}

        if existing:
            await db.execute(
                """
                UPDATE files
                SET schema_version = ?,
                    status = 'uploaded',
                    pages_total = NULL,
                    pages_done = 0,
                    error = NULL,
                    processing_started_at = NULL,
                    processed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE hash = ?
                """,
                (EXTRACTION_SCHEMA_VERSION, file_hash),
            )
        else:
            await db.execute(
                """
                INSERT INTO files (
                    hash, schema_version, status, pages_total, pages_done,
                    error, processing_started_at, processed_at, updated_at
                ) VALUES (?, ?, 'uploaded', NULL, 0, NULL, NULL, NULL, CURRENT_TIMESTAMP)
                """,
                (file_hash, EXTRACTION_SCHEMA_VERSION),
            )
        await db.commit()

    return {
        "hash": file_hash,
        "status": "new",
        "message": (
            "It appears this is a new exam schedule and can't be found on the database. "
            f"Process file now? (Estimated time {ESTIMATED_PROCESSING_MINUTES} Minutes)"
        ),
    }


@router.post("/process")
async def process(file: UploadFile = File(...), hash: str = Form(...)):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    file_hash = hashlib.sha256(contents).hexdigest()
    if file_hash != hash:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File hash mismatch.")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            active_row = await _fetch_file_row(db, file_hash)
            async with db.execute(
                "SELECT * FROM files WHERE status = 'processing' LIMIT 1"
            ) as cursor:
                other_active = await cursor.fetchone()

            if other_active and other_active["hash"] != file_hash:
                if not _has_active_processing_task(other_active["hash"]):
                    await _delete_file_artifacts(other_active["hash"], db)
                    other_active = None
                else:
                    await db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "busy",
                            "message": "Another PDF is already being processed. Please wait",
                            "activeHash": other_active["hash"],
                        },
                    )

            if active_row and active_row["status"] == "processing":
                if not _has_active_processing_task(file_hash):
                    await _delete_file_artifacts(file_hash, db)
                    active_row = None
                else:
                    await db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "already_processing",
                            "message": "This pdf is already being processed. Please wait",
                            "activeHash": file_hash,
                        },
                    )

            if active_row is None:
                await db.execute(
                    """
                    INSERT INTO files (
                        hash, schema_version, status, pages_total, pages_done,
                        error, processing_started_at, processed_at, updated_at
                    ) VALUES (?, ?, 'processing', NULL, 0, NULL, CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP)
                    """,
                    (file_hash, EXTRACTION_SCHEMA_VERSION),
                )
            else:
                await db.execute(
                    """
                    UPDATE files
                    SET schema_version = ?,
                        status = 'processing',
                        pages_total = NULL,
                        pages_done = 0,
                        error = NULL,
                        processing_started_at = CURRENT_TIMESTAMP,
                        processed_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE hash = ?
                    """,
                    (EXTRACTION_SCHEMA_VERSION, file_hash),
                )

            await db.execute("DELETE FROM schedules WHERE file_hash = ?", (file_hash,))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    start_processing_job(file_hash, contents)

    return {
        "hash": file_hash,
        "status": "processing_started",
        "message": "Processing started",
    }


@router.post("/processing/{file_hash}/cancel")
async def cancel_processing(file_hash: str):
    from state import PROCESSING_TASKS

    task = PROCESSING_TASKS.get(file_hash)
    if task and not task.done():
        task.cancel()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await _fetch_file_row(db, file_hash)
        if not row:
            return {
                "hash": file_hash,
                "status": "not_found",
                "deleted": False,
            }

        if row["status"] == "ready":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Completed schedules cannot be cancelled.",
            )

        await _delete_file_artifacts(file_hash, db)
        await db.commit()

    return {
        "hash": file_hash,
        "status": "cancelled",
        "deleted": True,
    }


@router.get("/processing/{file_hash}")
async def processing_status(file_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await _fetch_file_row(db, file_hash)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Processing job not found.")
    return {
        "hash": file_hash,
        **_processing_payload("Processing status", row),
    }


@router.post("/schedule")
async def schedule(payload: ScheduleRequest):
    if not payload.hash or not payload.classCode:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing hash or class code.")

    normalized_code = normalize_lookup_value(payload.classCode)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT *
            FROM schedules
            WHERE file_hash = ?
            """,
            (payload.hash,),
        )
        rows = await cursor.fetchall()

    serialized_rows = [
        normalize_schedule_row(dict(row))
        for row in rows
        if normalize_lookup_value(row["course_year"]) == normalized_code
    ]
    return {
        "hash": payload.hash,
        "classCode": payload.classCode,
        "rows": serialized_rows,
        "events": rows_to_events(serialized_rows),
    }
