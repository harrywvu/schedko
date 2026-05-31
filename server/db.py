from datetime import datetime

import asyncpg

from config import DATABASE_URL
from state import PROCESSING_TASKS

_POOL: asyncpg.Pool | None = None


def _require_pool() -> asyncpg.Pool:
    if _POOL is None:
        raise RuntimeError("Database pool has not been initialized.")
    return _POOL


async def get_pool() -> asyncpg.Pool:
    return _require_pool()


async def init_db():
    global _POOL

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is required.")

    if _POOL is None:
        _POOL = await asyncpg.create_pool(DATABASE_URL)

    async with _POOL.acquire() as db:
        async with db.transaction():
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
                    id BIGSERIAL PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    subject TEXT,
                    class_time TEXT,
                    class_days TEXT,
                    exam_time TEXT,
                    exam_day TEXT,
                    course_year TEXT,
                    instructor TEXT,
                    examiner TEXT,
                    exam_room TEXT,
                    exam_building TEXT,
                    major_exam TEXT,
                    semester TEXT,
                    academic_year TEXT,
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


async def close_db():
    global _POOL

    if _POOL is not None:
        await _POOL.close()
        _POOL = None


async def _get_table_columns(db, table_name: str) -> set[str]:
    rows = await db.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = $1
        """,
        table_name,
    )
    return {row["column_name"] for row in rows}


async def _ensure_column(db, table_name: str, column_name: str, column_ddl: str):
    columns = await _get_table_columns(db, table_name)
    if column_name not in columns:
        await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_ddl}")


async def _fetch_file_row(db, file_hash: str):
    return await db.fetchrow("SELECT * FROM files WHERE hash = $1", file_hash)


async def _has_schedule_rows(db, file_hash: str) -> bool:
    row = await db.fetchrow(
        "SELECT 1 FROM schedules WHERE file_hash = $1 LIMIT 1",
        file_hash,
    )
    return row is not None


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
                started_at = row["processing_started_at"]
                if not isinstance(started_at, datetime):
                    started_at = datetime.fromisoformat(str(started_at))
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
    pool = _require_pool()
    async with pool.acquire() as db:
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params = []

        if status_value is not None:
            updates.append(f"status = ${len(params) + 1}")
            params.append(status_value)
        if pages_total is not None:
            updates.append(f"pages_total = ${len(params) + 1}")
            params.append(pages_total)
        if pages_done is not None:
            updates.append(f"pages_done = ${len(params) + 1}")
            params.append(pages_done)
        if error is not None:
            updates.append(f"error = ${len(params) + 1}")
            params.append(error)
        if processing_started_at:
            updates.append("processing_started_at = CURRENT_TIMESTAMP")
        if processed_at:
            updates.append("processed_at = CURRENT_TIMESTAMP")

        params.append(file_hash)
        await db.execute(
            f"UPDATE files SET {', '.join(updates)} WHERE hash = ${len(params)}",
            *params,
        )


async def _delete_file_artifacts(file_hash: str, db=None):
    if db is not None:
        await db.execute("DELETE FROM schedules WHERE file_hash = $1", file_hash)
        await db.execute("DELETE FROM files WHERE hash = $1", file_hash)
        return

    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM schedules WHERE file_hash = $1", file_hash)
            await conn.execute("DELETE FROM files WHERE hash = $1", file_hash)


def _has_active_processing_task(file_hash: str) -> bool:
    task = PROCESSING_TASKS.get(file_hash)
    return bool(task and not task.done())
