import hashlib
import tempfile
import os
import aiosqlite
from fastapi import FastAPI, UploadFile, File
from pdf2image import convert_from_bytes
import base64
import httpx
import json
from io import BytesIO
import asyncio
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
semaphore = asyncio.Semaphore(10)
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
            CREATE TABLE IF NOT EXISTS files (
                hash TEXT PRIMARY KEY,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    contents = await file.read()
    file_hash = hashlib.sha256(contents).hexdigest()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT hash FROM files WHERE hash = ?", (file_hash,)) as cursor:
            existing = await cursor.fetchone()

    if existing:
        return {"hash": file_hash, "status": "cached"}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO files (hash) VALUES (?)", (file_hash,))
        await db.commit()

    images = convert_from_bytes(contents, dpi=200)

    async with httpx.AsyncClient() as client:
        tasks = [extract_page(client, image) for image in images]
        results = await asyncio.gather(*tasks)

    all_rows = [row for page in results for row in page]
    print(f"Extracted {len(all_rows)} rows total")

    return {"hash": file_hash, "status": "new", "pages": len(images), "rows": len(all_rows)}

async def extract_page(client: httpx.AsyncClient, image: any) -> list:
    async with semaphore:
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

        response = await client.post(GEMINI_URL, json=payload, timeout=60)
        data = response.json()

        if "error" in data:
            print(f"Gemini error: {data['error']['message']}")
            return []

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)