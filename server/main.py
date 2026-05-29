import hashlib
import tempfile
import os
import re
from datetime import datetime, time
import aiosqlite
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pdf2image import convert_from_bytes
import base64
import httpx
import json
from io import BytesIO
import asyncio
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"

EXTRACTION_PROMPT = """
Extract the needed keys from every row from this exam schedule table as a JSON array.
Each object must have these exact keys:
subject, class_time, class_days, exam_day, exam_time, course_year, instructor, examiner, exam_room, exam_building, major_exam, semester, academic_year.
exam_day and exam_time, can be found on the greyed out row. major_exam is whether or not its Midterms or Finals.
For rows where the subject cell is blank (merged from above), carry forward the last subject value.
Return only the JSON array, no markdown, no explanation.
"""

MAX_ATTEMPTS_TO_RETRY = 3
EXTRACTION_SCHEMA_VERSION = 2
ESTIMATED_PROCESSING_MINUTES = "4-5"
BACKGROUND_TASKS = set()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
DB_PATH = "schedko.db"

async def _get_table_columns(db, table_name: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _ensure_column(db, table_name: str, column_name: str, column_ddl: str):
    columns = await _get_table_columns(db, table_name)
    if column_name not in columns:
        await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_ddl}")


async def _fetch_file_row(db, file_hash: str):
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM files WHERE hash = ?", (file_hash,)) as cursor:
        return await cursor.fetchone()


async def _has_schedule_rows(db, file_hash: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM schedules WHERE file_hash = ? LIMIT 1",
        (file_hash,),
    ) as cursor:
        return await cursor.fetchone() is not None


def _processing_payload(message: str, row=None) -> dict:
    payload = {
        "message": message,
        "status": row["status"] if row else "processing",
        "pages_total": row["pages_total"] if row else None,
        "pages_done": row["pages_done"] if row else 0,
        "progress_percent": 0,
        "estimated_remaining_seconds": None,
    }

    if row and row["pages_total"]:
        pages_total = int(row["pages_total"])
        pages_done = int(row["pages_done"] or 0)
        payload["progress_percent"] = round((pages_done / pages_total) * 100) if pages_total else 0
        if row["processing_started_at"] and pages_done > 0 and pages_done < pages_total:
            try:
                started_at = datetime.fromisoformat(str(row["processing_started_at"]))
                elapsed_seconds = max((datetime.utcnow() - started_at).total_seconds(), 1)
                per_page = elapsed_seconds / pages_done
                payload["estimated_remaining_seconds"] = int(per_page * (pages_total - pages_done))
            except ValueError:
                payload["estimated_remaining_seconds"] = None

    return payload


async def _set_file_state(
    file_hash: str,
    *,
    status_value: str | None = None,
    pages_total: int | None = None,
    pages_done: int | None = None,
    error: str | None = None,
    processing_started_at: bool = False,
    processed_at: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params = []

        if status_value is not None:
            updates.append("status = ?")
            params.append(status_value)
        if pages_total is not None:
            updates.append("pages_total = ?")
            params.append(pages_total)
        if pages_done is not None:
            updates.append("pages_done = ?")
            params.append(pages_done)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if processing_started_at:
            updates.append("processing_started_at = CURRENT_TIMESTAMP")
        if processed_at:
            updates.append("processed_at = CURRENT_TIMESTAMP")

        params.append(file_hash)
        await db.execute(
            f"UPDATE files SET {', '.join(updates)} WHERE hash = ?",
            params,
        )
        await db.commit()


def _normalize_schedule_row(row: dict) -> dict:
    exam_day = row.get("exam_day") or row.get("class_day") or row.get("class_days") or row.get("day")
    exam_time = row.get("exam_time") or row.get("class_time")

    return {
        "file_hash": row.get("file_hash"),
        "subject": row.get("subject"),
        "class_time": exam_time,
        "class_day": exam_day,
        "class_days": row.get("class_days") or exam_day,
        "exam_time": exam_time,
        "exam_day": exam_day,
        "course_year": row.get("course_year"),
        "num_students": row.get("num_students"),
        "instructor": row.get("instructor"),
        "examiner": row.get("examiner"),
        "exam_room": row.get("exam_room") or row.get("room"),
        "exam_building": row.get("exam_building") or row.get("building"),
        "major_exam": row.get("major_exam"),
        "semester": row.get("semester"),
        "academic_year": row.get("academic_year"),
        "day": exam_day,
        "room": row.get("exam_room") or row.get("room"),
        "building": row.get("exam_building") or row.get("building"),
    }


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                hash TEXT PRIMARY KEY,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                schema_version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'uploaded',
                pages_total INTEGER,
                pages_done INTEGER DEFAULT 0,
                error TEXT,
                processing_started_at TIMESTAMP,
                processed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT NOT NULL,
                subject TEXT,
                class_time TEXT,
                class_day TEXT,
                class_days TEXT,
                exam_time TEXT,
                exam_day TEXT,
                course_year TEXT,
                num_students TEXT,
                instructor TEXT,
                examiner TEXT,
                exam_room TEXT,
                exam_building TEXT,
                major_exam TEXT,
                semester TEXT,
                academic_year TEXT,
                day TEXT,
                room TEXT,
                building TEXT,
                FOREIGN KEY (file_hash) REFERENCES files(hsh)
            )
        """)
        await _ensure_column(db, "files", "schema_version", "schema_version INTEGER NOT NULL DEFAULT 1")
        await _ensure_column(db, "files", "status", "status TEXT NOT NULL DEFAULT 'uploaded'")
        await _ensure_column(db, "files", "pages_total", "pages_total INTEGER")
        await _ensure_column(db, "files", "pages_done", "pages_done INTEGER DEFAULT 0")
        await _ensure_column(db, "files", "error", "error TEXT")
        await _ensure_column(db, "files", "processing_started_at", "processing_started_at TIMESTAMP")
        await _ensure_column(db, "files", "processed_at", "processed_at TIMESTAMP")
        await _ensure_column(db, "files", "updated_at", "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        await _ensure_column(db, "schedules", "class_day", "class_day TEXT")
        await _ensure_column(db, "schedules", "class_days", "class_days TEXT")
        await _ensure_column(db, "schedules", "exam_time", "exam_time TEXT")
        await _ensure_column(db, "schedules", "exam_day", "exam_day TEXT")
        await _ensure_column(db, "schedules", "exam_room", "exam_room TEXT")
        await _ensure_column(db, "schedules", "exam_building", "exam_building TEXT")
        await _ensure_column(db, "schedules", "major_exam", "major_exam TEXT")
        await _ensure_column(db, "schedules", "semester", "semester TEXT")
        await _ensure_column(db, "schedules", "academic_year", "academic_year TEXT")
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hash_course
            ON schedules(file_hash, course_year)
        """)
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

class ScheduleRequest(BaseModel):
    hash: str
    classCode: str


def _normalize_lookup_value(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _coalesce_row_value(row: dict, *keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_day_code(day_value: str) -> list[int]:
    normalized = "".join(ch for ch in str(day_value or "").upper() if ch.isalpha())
    if not normalized:
        return []

    if "MWF" in normalized:
        return [1, 3, 5]

    if "TTH" in normalized:
        return [2, 4]

    days = []
    index = 0
    weekday_map = {
        "M": 1,
        "T": 2,
        "W": 3,
        "TH": 4,
        "F": 5,
        "S": 6,
        "SU": 0,
    }

    while index < len(normalized):
        token2 = normalized[index:index + 2]
        if token2 in ("TH", "SU"):
            days.append(weekday_map[token2])
            index += 2
            continue

        token = normalized[index]
        if token in weekday_map:
            days.append(weekday_map[token])
        index += 1

    return sorted(set(days))


def _parse_exam_date(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None

    candidates = [raw]
    if "," in raw:
        candidates.append(raw.split(",", 1)[1].strip())
    if " " in raw:
        candidates.append(raw)

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
    ]

    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            pass

        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue

    return None


TIME_PART_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*$", re.IGNORECASE)


def _parse_time_segment(segment: str):
    match = TIME_PART_RE.match(str(segment or ""))
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = (match.group(3) or "").lower()

    if suffix == "pm" and hour != 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    elif not suffix and 1 <= hour <= 6:
        hour += 12

    return hour, minute


def _parse_time_range(class_time: str):
    if not class_time:
        return None

    parts = str(class_time).split("-", 1)
    if len(parts) != 2:
        return None

    start = _parse_time_segment(parts[0])
    end = _parse_time_segment(parts[1])
    if not start or not end:
        return None

    start_hour, start_minute = start
    end_hour, end_minute = end

    if end_hour < start_hour and start[0] < 12 and end[0] <= 12 and "am" not in str(parts[1]).lower() and "pm" not in str(parts[1]).lower():
        end_hour += 12

    return (start_hour, start_minute), (end_hour, end_minute)


def _build_datetime(date_value, hour: int, minute: int) -> str:
    return datetime.combine(date_value, time(hour=hour, minute=minute)).isoformat()


def rows_to_events(rows: list[dict]) -> list[dict]:
    events = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue

        exam_day_value = _coalesce_row_value(
            row,
            "exam_day",
            "examDay",
            "class_day",
            "class_days",
            "day",
        )
        exam_time_value = _coalesce_row_value(
            row,
            "exam_time",
            "examTime",
            "class_time",
        )
        time_range = _parse_time_range(exam_time_value)
        exam_date = _parse_exam_date(exam_day_value)
        days_of_week = _normalize_day_code(exam_day_value)

        if not time_range:
            continue

        (start_hour, start_minute), (end_hour, end_minute) = time_range
        has_date = exam_date is not None
        has_recurring_days = bool(days_of_week)
        title = row.get("subject") or row.get("course_year") or row.get("major_exam") or "Exam"
        events.append(
            {
                "id": "-".join(
                    str(part)
                    for part in (
                        row.get("file_hash") or row.get("hash") or "schedko",
                        row.get("course_year") or "course",
                        row.get("subject") or "subject",
                        exam_time_value or "time",
                        exam_day_value or "day",
                        index,
                    )
                ),
                "title": title,
                **(
                    {
                        "start": _build_datetime(exam_date, start_hour, start_minute),
                        "end": _build_datetime(exam_date, end_hour, end_minute),
                    }
                    if has_date
                    else {}
                ),
                **(
                    {
                        "daysOfWeek": days_of_week,
                        "startTime": f"{start_hour:02d}:{start_minute:02d}:00",
                        "endTime": f"{end_hour:02d}:{end_minute:02d}:00",
                    }
                    if not has_date and has_recurring_days
                    else {}
                ),
                "extendedProps": {
                    **row,
                    "exam_day": exam_day_value,
                    "exam_time": exam_time_value,
                },
            }
        )

    return events


@app.post("/upload")
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
        "message": "It appears this is a new exam schedule and can't be found on the database. Process file now? (Estimated time 4-5 Minutes)",
    }


@app.post("/process")
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

    task = asyncio.create_task(process_pdf_job(file_hash, contents))
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)

    return {
        "hash": file_hash,
        "status": "processing_started",
        "message": "Processing started",
    }


@app.get("/processing/{file_hash}")
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


@app.post("/schedule")
async def schedule(payload: ScheduleRequest):
    if not payload.hash or not payload.classCode:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing hash or class code.")

    normalized_code = _normalize_lookup_value(payload.classCode)

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
        dict(row)
        for row in rows
        if _normalize_lookup_value(row["course_year"]) == normalized_code
    ]
    return {
        "hash": payload.hash,
        "classCode": payload.classCode,
        "rows": serialized_rows,
        "events": rows_to_events(serialized_rows),
    }


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
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
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

                schedule_row = _normalize_schedule_row({**row, "file_hash": file_hash})
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
                rows_to_insert
            )
            await db.commit()
    except aiosqlite.Error as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DB error: {exc}")
