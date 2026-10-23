"""Validated direct-PDF extraction shared by the API and benchmark."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
import json
import math
import random
import re
import resource
import time
from zoneinfo import ZoneInfo

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from extraction_prompt import direct_pdf_prompt

API_ROOT = "https://generativelanguage.googleapis.com"
MODEL = "gemini-3.5-flash-lite"
ROW_FIELDS = (
    "subject", "class_time", "class_days", "course_year", "instructor",
    "examiner", "exam_room", "exam_building",
)
SECTION_FIELDS = ("exam_day", "exam_time", "major_exam", "semester", "academic_year")
ALL_FIELDS = ROW_FIELDS + SECTION_FIELDS


class ExtractionError(Exception):
    """Safe to display: never includes provider bodies, URLs, or PDF contents."""


class InvalidExtraction(ExtractionError):
    pass


class QuotaExhausted(ExtractionError):
    pass


def pdf_page_count(contents):
    if not contents or len(contents) > 50_000_000:
        raise ExtractionError("Upload a nonempty PDF smaller than 50 MB.")
    try:
        reader = PdfReader(BytesIO(contents))
        if reader.is_encrypted:
            raise ExtractionError("Password-protected PDFs are not supported.")
        count = len(reader.pages)
    except (PdfReadError, ValueError, TypeError, KeyError) as exc:
        raise ExtractionError("The uploaded file could not be read as a PDF.") from exc
    if not 1 <= count <= 1000:
        raise ExtractionError("PDFs must contain between 1 and 1000 pages.")
    return count


def object_schema(properties):
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


NULLABLE_TEXT = {"type": ["string", "null"]}
RESPONSE_SCHEMA = object_schema({
    "pages": {"type": "array", "items": object_schema({
        "page": {"type": "integer"},
        "kind": {"type": "string", "enum": ["schedule", "no_schedule"]},
        "sections": {"type": "array", "items": object_schema({
            **{key: NULLABLE_TEXT for key in SECTION_FIELDS},
            "major_exam": {
                "type": ["string", "null"], "enum": ["Finals", "Midterms", None],
                "description": "Examination type from the title, never a subject code.",
            },
            "rows": {
                "type": "array", "minItems": 1,
                "items": {"type": "array", "minItems": 8, "maxItems": 8,
                          "items": NULLABLE_TEXT},
            },
        })},
    })},
})


def validate_pages(data, expected_pages):
    """Expand compact rows without guessing missing dates, times, or subjects."""
    try:
        if not isinstance(data, dict) or set(data) != {"pages"}:
            raise ValueError
        if not isinstance(data["pages"], list):
            raise ValueError
        pages = {}
        for page in data["pages"]:
            if not isinstance(page, dict) or set(page) != {"page", "kind", "sections"}:
                raise ValueError
            number = page["page"]
            if type(number) is not int or number in pages or number not in expected_pages:
                raise ValueError
            sections = page["sections"]
            if not isinstance(sections, list) or page["kind"] not in ("schedule", "no_schedule"):
                raise ValueError
            if (page["kind"] == "schedule") != bool(sections):
                raise ValueError
            rows = []
            for section in sections:
                if not isinstance(section, dict) or set(section) != {*SECTION_FIELDS, "rows"}:
                    raise ValueError
                metadata = {key: section[key] for key in SECTION_FIELDS}
                if any(value is not None and not isinstance(value, str) for value in metadata.values()):
                    raise ValueError
                if metadata["major_exam"] not in ("Finals", "Midterms", None):
                    raise ValueError
                if not isinstance(section["rows"], list) or not section["rows"]:
                    raise ValueError
                for values in section["rows"]:
                    if not isinstance(values, list) or len(values) != len(ROW_FIELDS):
                        raise ValueError
                    if any(value is not None and not isinstance(value, str) for value in values):
                        raise ValueError
                    # Unknown fields are allowed, but an entirely empty row is not.
                    if not any(value and value.strip() for value in values):
                        raise ValueError
                    # A common vision failure is retaining the student-count
                    # column and shifting the instructor/examiner/room fields.
                    if any(value is not None and value.strip().isdigit() for value in values[4:6]):
                        raise ValueError
                    rows.append({**dict(zip(ROW_FIELDS, values)), **metadata})
            pages[number] = rows
        if set(pages) != set(expected_pages):
            raise ValueError
        return pages
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidExtraction("Invalid or incomplete page extraction.") from exc


def retry_details(response):
    """Inspect structured quota metadata; do not retain or log error bodies."""
    delay = 0.0
    header = response.headers.get("Retry-After", "")
    try:
        delay = float(header)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            pass
    daily = False
    try:
        details = response.json().get("error", {}).get("details", [])
        for detail in details:
            value = detail.get("retryDelay", "")
            if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
                delay = max(delay, float(value[:-1]))
            for violation in detail.get("violations", []):
                identifier = str(violation.get("quotaId", "")) + str(violation.get("quotaMetric", ""))
                compact = re.sub(r"[^a-z]", "", identifier.lower())
                daily |= "perday" in compact or "daily" in compact
    except (ValueError, TypeError, AttributeError):
        pass
    return (max(0.0, delay) if math.isfinite(delay) else 0.0), daily


class RequestLimiter:
    """One process-wide generation budget, including retries and overlapping jobs.

    Budgets are local, not an attempt to infer usage by other applications.
    The benchmark uses a fixed allowance; the API resets its local daily budget
    at midnight Pacific, matching Google's daily reset.
    """

    def __init__(self, rpm, tpm, remaining_requests, *, interval=13.0,
                 clock=time.monotonic, sleep=asyncio.sleep, reset_daily=False,
                 today=None):
        if min(rpm, tpm, remaining_requests) <= 0 or interval < 0:
            raise ValueError("Quotas must be positive and spacing must be nonnegative.")
        self.rpm, self.tpm = rpm, tpm
        self.remaining = remaining_requests
        self.daily_limit = remaining_requests
        self.today = today or (lambda: datetime.now(ZoneInfo("America/Los_Angeles")).date())
        self.reset_daily = reset_daily
        self.budget_day = self.today()
        self.interval = max(interval, 60.0 / rpm + 0.05)
        self.clock, self.sleep = clock, sleep
        self.recent = deque()
        self.last_start = -math.inf
        self.blocked_until = 0.0
        self.lock = asyncio.Lock()

    def defer(self, seconds):
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)

    async def acquire(self, tokens):
        if tokens <= 0 or tokens > self.tpm:
            raise QuotaExhausted("The document exceeds the configured input-token allowance per minute.")
        async with self.lock:
            while True:
                day = self.today()
                if self.reset_daily and day != self.budget_day:
                    self.remaining = self.daily_limit
                    self.budget_day = day
                if self.remaining <= 0:
                    raise QuotaExhausted("The local daily request allowance is exhausted. Re-upload after the quota resets to resume.")
                now = self.clock()
                while self.recent and now - self.recent[0][0] >= 60:
                    self.recent.popleft()
                until = max(self.last_start + self.interval, self.blocked_until)
                if len(self.recent) >= self.rpm or sum(item[1] for item in self.recent) + tokens > self.tpm:
                    until = max(until, self.recent[0][0] + 60)
                if until > now:
                    await self.sleep(until - now)
                    continue
                self.last_start = now
                self.recent.append((now, tokens))
                self.remaining -= 1
                return


class PdfExtractor:
    def __init__(self, client, api_key, limiter, *, model=MODEL, events=None, on_status=None):
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", model):
            raise ValueError("Invalid model identifier.")
        self.client, self.limiter, self.model = client, limiter, model
        self.headers = {"x-goog-api-key": api_key}
        self.events = events if events is not None else []
        self.on_status = on_status
        self.slots = asyncio.Semaphore(2)

    async def request(self, method, url, **kwargs):
        try:
            response = await self.client.request(method, url, headers=self.headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ExtractionError("Gemini file/token request failed; check network connectivity.") from exc
        if response.is_error:
            raise ExtractionError(f"Gemini file/token request failed (HTTP {response.status_code}).")
        return response

    @asynccontextmanager
    async def uploaded_pdf(self, contents):
        """Upload once, wait for ACTIVE, and clean up even on cancellation."""
        name = None
        started = time.monotonic()
        try:
            async with asyncio.timeout(150):
                response = await self.client.post(
                    f"{API_ROOT}/upload/v1beta/files",
                    headers={**self.headers, "X-Goog-Upload-Protocol": "resumable",
                             "X-Goog-Upload-Command": "start",
                             "X-Goog-Upload-Header-Content-Length": str(len(contents)),
                             "X-Goog-Upload-Header-Content-Type": "application/pdf"},
                    json={"file": {"display_name": "schedule-pdf"}},
                )
                if response.is_error:
                    raise ExtractionError(f"PDF upload initiation failed (HTTP {response.status_code}).")
                upload_url = response.headers.get("X-Goog-Upload-URL", "")
                # Do not send credentials to an arbitrary upload URL.
                parsed = httpx.URL(upload_url)
                if parsed.scheme != "https" or parsed.host != "generativelanguage.googleapis.com":
                    raise ExtractionError("Unexpected PDF upload destination.")
                response = await self.client.post(
                    upload_url, content=contents,
                    headers={**self.headers, "X-Goog-Upload-Offset": "0",
                             "X-Goog-Upload-Command": "upload, finalize",
                             "Content-Type": "application/pdf"},
                )
                if response.is_error:
                    raise ExtractionError(f"PDF upload failed (HTTP {response.status_code}).")
                file = response.json()["file"]
                name = file["name"]
                if not re.fullmatch(r"files/[a-zA-Z0-9-]+", name):
                    name = None
                    raise ExtractionError("Unexpected PDF file identifier.")
                while file.get("state") == "PROCESSING":
                    await asyncio.sleep(1)
                    file = (await self.request("GET", f"{API_ROOT}/v1beta/{name}")).json()
                if file.get("state") != "ACTIVE" or not file.get("uri"):
                    raise ExtractionError("Gemini could not prepare the PDF.")
            self.events.append({"event": "upload", "seconds": time.monotonic() - started,
                                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
            yield file["uri"]
        except (httpx.HTTPError, TimeoutError, KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ExtractionError("PDF upload/preparation failed.") from exc
        finally:
            if name:
                try:
                    async with asyncio.timeout(15):
                        await self.request("DELETE", f"{API_ROOT}/v1beta/{name}")
                except (ExtractionError, TimeoutError):
                    self.events.append({"event": "remote_cleanup_failed"})

    def payload(self, uri, first, last):
        return {
            "contents": [{"role": "user", "parts": [
                {"fileData": {"mimeType": "application/pdf", "fileUri": uri}},
                {"text": direct_pdf_prompt(first, last)},
            ]}],
            "generationConfig": {
                "responseMimeType": "application/json", "responseJsonSchema": RESPONSE_SCHEMA,
                "thinkingConfig": {"thinkingLevel": "MINIMAL"}, "maxOutputTokens": 65536,
            },
        }

    async def extract_range(self, uri, first, last):
        payload = self.payload(uri, first, last)
        endpoint = f"{API_ROOT}/v1beta/models/{self.model}"
        counted = await self.request("POST", f"{endpoint}:countTokens", json={
            "generateContentRequest": {"model": f"models/{self.model}", **payload},
        })
        try:
            tokens = counted.json()["totalTokens"]
            if type(tokens) is not int or tokens <= 0:
                raise ValueError
        except (KeyError, ValueError, TypeError) as exc:
            raise ExtractionError("Gemini did not return a usable input-token count.") from exc
        for attempt in range(3):
            async with self.slots:
                waiting = time.monotonic()
                await self.limiter.acquire(tokens)
                if self.on_status:
                    await self.on_status("extracting", 0)
                started = time.monotonic()
                event = {"event": "generation", "first": first, "last": last,
                         "attempt": attempt + 1, "input_tokens_reserved": tokens,
                         "peak_rss_before_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                         "wait_seconds": started - waiting}
                self.events.append(event)
                try:
                    async with asyncio.timeout(150):
                        response = await self.client.post(
                            f"{endpoint}:generateContent", json=payload, headers=self.headers,
                        )
                    event["http_status"] = response.status_code
                    if not response.is_error:
                        try:
                            data = response.json()
                            candidate = data["candidates"][0]
                            event["usage"] = {
                                key: value for key, value in data.get("usageMetadata", {}).items()
                                if key in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount")
                                and type(value) is int
                            }
                            if candidate.get("finishReason") != "STOP":
                                raise ValueError
                            result = "".join(part.get("text", "") for part in candidate["content"]["parts"]
                                             if not part.get("thought"))
                            return validate_pages(json.loads(result), range(first, last + 1))
                        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
                            raise InvalidExtraction("Incomplete or invalid Gemini response.") from exc
                    delay, daily = retry_details(response)
                    if daily and response.status_code == 429:
                        raise QuotaExhausted("Gemini reports daily quota exhaustion; retry after the quota resets.")
                    if response.status_code not in (408, 429, 500, 502, 503, 504):
                        raise ExtractionError(f"Gemini rejected extraction (HTTP {response.status_code}).")
                    event["retry_reason"] = f"http_{response.status_code}"
                    delay = max(delay, 30 if response.status_code == 429 else 2 ** (attempt + 1))
                except (httpx.TransportError, TimeoutError):
                    event["retry_reason"] = "network_or_timeout"
                    delay = 2 ** (attempt + 1)
                finally:
                    event["seconds"] = time.monotonic() - started
                    event["peak_rss_after_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                if attempt < 2:
                    delay += random.uniform(0, 1)
                    self.limiter.defer(delay)
                    if self.on_status:
                        await self.on_status("waiting", math.ceil(delay))
        raise ExtractionError("Extraction failed after three attempts; no partial result was accepted.")

    async def extract(self, uri, page_count, strategy, *, existing_pages=None, on_pages=None):
        if strategy not in ("full", "split") or page_count < 1:
            raise ValueError("Invalid extraction strategy or page count.")
        existing_pages = existing_pages or {}

        async def run(first, last):
            try:
                pages = await self.extract_range(uri, first, last)
            except InvalidExtraction:
                if first == last:
                    raise
                self.events.append({"event": "split", "first": first, "last": last})
                middle = (first + last) // 2
                # Sequential fallback bounds task growth and keeps the two-slot cap.
                return {**await run(first, middle), **await run(middle + 1, last)}
            if on_pages:
                await on_pages(pages)
            return pages

        ranges = [(1, page_count)]
        if strategy == "split" and page_count > 1:
            middle = (page_count + 1) // 2
            ranges = [(1, middle), (middle + 1, page_count)]
        missing_ranges = []
        for first, last in ranges:
            start = None
            for number in range(first, last + 2):
                if number <= last and number not in existing_pages:
                    if start is None:
                        start = number
                elif start is not None:
                    missing_ranges.append((start, number - 1))
                    start = None

        # At most two range workers, even with fragmented saved checkpoints.
        queue = asyncio.Queue()
        for item in missing_ranges:
            queue.put_nowait(item)

        async def worker():
            pages = {}
            while not queue.empty():
                first, last = queue.get_nowait()
                pages.update(await run(first, last))
            return pages

        tasks = [asyncio.create_task(worker()) for _ in range(min(2, len(missing_ranges)))]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        pages = {**existing_pages, **{number: rows for result in results for number, rows in result.items()}}
        if set(pages) != set(range(1, page_count + 1)):
            raise InvalidExtraction("Extraction did not cover every page.")
        return {number: pages[number] for number in sorted(pages)}
