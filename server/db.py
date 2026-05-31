from datetime import datetime

import aiosqlite

from config import DB_PATH
from state import PROCESSING_TASKS


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


async def _delete_file_artifacts(file_hash: str, db=None):
    if db is not None:
        await db.execute("DELETE FROM schedules WHERE file_hash = ?", (file_hash,))
        await db.execute("DELETE FROM files WHERE hash = ?", (file_hash,))
        return

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM schedules WHERE file_hash = ?", (file_hash,))
        await conn.execute("DELETE FROM files WHERE hash = ?", (file_hash,))
        await conn.commit()


def _has_active_processing_task(file_hash: str) -> bool:
    task = PROCESSING_TASKS.get(file_hash)
    return bool(task and not task.done())


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
                FOREIGN KEY (file_hash) REFERENCES files(hash)
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
