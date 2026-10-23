import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_PDF_MODEL = os.getenv("GEMINI_PDF_MODEL", "gemini-3.5-flash-lite")
PDF_PROCESSING_MODE = os.getenv("PDF_PROCESSING_MODE", "pdf")
PDF_EXTRACTION_STRATEGY = os.getenv("PDF_EXTRACTION_STRATEGY", "split")
# Local budgets, not claims about the project's current Google quotas.
GEMINI_RPM = int(os.getenv("GEMINI_RPM", "5"))
GEMINI_INPUT_TPM = int(os.getenv("GEMINI_INPUT_TPM", "250000"))
GEMINI_RPD = int(os.getenv("GEMINI_RPD", "20"))
PDF_PIPELINE_VERSION = f"pdf-v2:{GEMINI_PDF_MODEL}:1"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
)

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
