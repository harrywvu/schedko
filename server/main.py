import hashlib
import tempfile
import os
import aiosqlite
from fastapi import FastAPI, UploadFile, File, HTTPException, status
from pdf2image import convert_from_bytes
import base64
import httpx
import json
from io import BytesIO
import asyncio
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"

EXTRACTION_PROMPT = """
Extract every row from this exam schedule table as a JSON array.
Each object must have these exact keys:
subject, class_time, day, course_year, num_students, instructor, examiner, room, building.
For rows where the subject cell is blank (merged from above), carry forward the last subject value.
Return only the JSON array, no markdown, no explanation.
"""

app = FastAPI()
DB_PATH = "schedko.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hash_course
            ON schedules(file_hash, course_year)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                hash TEXT PRIMARY KEY,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash TEXT NOT NULL,
                subject TEXT,
                class_time TEXT,
                day TEXT,
                course_year TEXT,
                num_students TEXT,
                instructor TEXT,
                examiner TEXT,
                room TEXT,
                building TEXT,
                FOREIGN KEY (file_hash) REFERENCES files(hash)
            )
        """)
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")
    file_hash = hashlib.sha256(contents).hexdigest()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT hash FROM files WHERE hash = ?", (file_hash,)) as cursor:
            existing = await cursor.fetchone()

    if existing:
        return {"hash": file_hash, "status": "cached"}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO files (hash) VALUES (?)", (file_hash,))
        await db.commit()

    try:
        images = convert_from_bytes(contents, dpi=200)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PDF file.")

    all_rows = await extract_all_pages(images)
    if not isinstance(all_rows, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Extraction failed.")
    print(f"Extracted {len(all_rows)} rows total")

    await persist_rows(file_hash, all_rows)

    return {"hash": file_hash, "status": "new", "pages": len(images), "rows": len(all_rows)}

async def extract_all_pages(images) -> list:
    all_rows = []
    async with httpx.AsyncClient() as client:
        for i, image in enumerate(images):
            print(f"Processing page {i + 1}/{len(images)}")
            rows = await extract_page(client, image)
            all_rows.extend(rows)
            if i < len(images) - 1:
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

    try:
        response = await client.post(GEMINI_URL, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        print(f"Gemini HTTP error: {exc}")
        return []
    except json.JSONDecodeError:
        print("Gemini response JSON decode error")
        return []

    if "error" in data:
        print(f"Gemini error: {data['error']['message']}")
        return []

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        print("Gemini response parsing error")
        return []

async def persist_rows(file_hash, all_rows):
    if not isinstance(all_rows, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Invalid rows format.")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            rows_to_insert = [
                (
                    file_hash,
                    row.get("subject"),
                    row.get("class_time"),
                    row.get("day"),
                    row.get("course_year"),
                    row.get("num_students"),
                    row.get("instructor"),
                    row.get("examiner"),
                    row.get("room"),
                    row.get("building"),
                )
                for row in all_rows if isinstance(row, dict)
            ]
            await db.executemany("INSERT INTO schedules (...) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_to_insert)
    except aiosqlite.Error as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DB error: {exc}")