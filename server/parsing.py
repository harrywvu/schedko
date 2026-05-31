import re
from datetime import datetime, time


def normalize_lookup_value(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def coalesce_row_value(row: dict, *keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def normalize_day_code(day_value: str) -> list[int]:
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


def parse_exam_date(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None

    candidates = [raw]
    without_parenthetical = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    if without_parenthetical and without_parenthetical != raw:
        candidates.append(without_parenthetical)
    if "," in raw:
        candidates.append(raw.split(",", 1)[0].strip())

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%b %d %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d %b, %Y",
        "%d %B, %Y",
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


def parse_time_segment(segment: str):
    normalized = str(segment or "").replace(".", "")
    match = TIME_PART_RE.match(normalized)
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


def parse_time_range(class_time: str):
    if not class_time:
        return None

    parts = str(class_time).split("-", 1)
    if len(parts) != 2:
        return None

    start = parse_time_segment(parts[0])
    end = parse_time_segment(parts[1])
    if not start or not end:
        return None

    start_hour, start_minute = start
    end_hour, end_minute = end

    if end_hour < start_hour and start[0] < 12 and end[0] <= 12 and "am" not in str(parts[1]).lower() and "pm" not in str(parts[1]).lower():
        end_hour += 12

    return (start_hour, start_minute), (end_hour, end_minute)


def build_datetime(date_value, hour: int, minute: int) -> str:
    return datetime.combine(date_value, time(hour=hour, minute=minute)).isoformat()


def normalize_schedule_row(row: dict) -> dict:
    return {
        "file_hash": row.get("file_hash"),
        "subject": row.get("subject"),
        "class_time": row.get("class_time") or row.get("classTime") or row.get("exam_time"),
        "class_days": row.get("class_days") or row.get("classDays") or row.get("class_day") or row.get("day"),
        "exam_time": row.get("exam_time") or row.get("examTime") or row.get("class_time"),
        "exam_day": row.get("exam_day") or row.get("examDay") or row.get("class_day") or row.get("day"),
        "course_year": row.get("course_year"),
        "instructor": row.get("instructor"),
        "examiner": row.get("examiner"),
        "exam_room": row.get("exam_room") or row.get("room"),
        "exam_building": row.get("exam_building") or row.get("building"),
        "major_exam": row.get("major_exam"),
        "semester": row.get("semester"),
        "academic_year": row.get("academic_year"),
    }


def rows_to_events(rows: list[dict]) -> list[dict]:
    events = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue

        exam_day_value = coalesce_row_value(row, "exam_day", "examDay", "class_day", "class_days", "day")
        exam_time_value = coalesce_row_value(row, "exam_time", "examTime", "class_time")
        time_range = parse_time_range(exam_time_value)
        exam_date = parse_exam_date(exam_day_value)
        days_of_week = normalize_day_code(exam_day_value)

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
                        "start": build_datetime(exam_date, start_hour, start_minute),
                        "end": build_datetime(exam_date, end_hour, end_minute),
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
