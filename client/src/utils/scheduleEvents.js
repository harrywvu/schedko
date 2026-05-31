const WEEKDAY_MAP = {
  M: 1,
  T: 2,
  W: 3,
  TH: 4,
  F: 5,
  S: 6,
  SU: 0,
};

function normalizeText(value) {
  return String(value || '').trim();
}

function flattenScheduleInput(scheduleInput) {
  if (Array.isArray(scheduleInput)) {
    return scheduleInput;
  }

  if (!scheduleInput || typeof scheduleInput !== 'object') {
    return [];
  }

  return Object.values(scheduleInput).flatMap((value) => {
    if (Array.isArray(value)) {
      return value;
    }

    return value && typeof value === 'object' ? [value] : [];
  });
}

function isAlreadyCalendarEvent(item) {
  return Boolean(item && (item.start || item.end || item.daysOfWeek));
}

function parseDateValue(value) {
  const raw = normalizeText(value);
  if (!raw) {
    return null;
  }

  const candidates = [raw];
  if (raw.includes(',')) {
    candidates.push(raw.split(',', 1)[1].trim());
  }

  for (const candidate of candidates) {
    const parsed = new Date(candidate);
    if (!Number.isNaN(parsed.getTime())) {
      return new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
    }

    const isoMatch = candidate.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (isoMatch) {
      return new Date(
        Number(isoMatch[1]),
        Number(isoMatch[2]) - 1,
        Number(isoMatch[3]),
      );
    }

    const slashMatch = candidate.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (slashMatch) {
      return new Date(
        Number(slashMatch[3]),
        Number(slashMatch[1]) - 1,
        Number(slashMatch[2]),
      );
    }
  }

  return null;
}

function parseTimeSegment(segment) {
  const raw = normalizeText(segment).toLowerCase();
  if (!raw) {
    return null;
  }

  const match = raw.match(/^(\d{1,2})(?::(\d{2}))?\s*([ap]m)?$/i);
  if (!match) {
    return null;
  }

  let hour = Number.parseInt(match[1], 10);
  const minute = Number.parseInt(match[2] || '0', 10);
  const suffix = (match[3] || '').toLowerCase();

  if (Number.isNaN(hour) || Number.isNaN(minute)) {
    return null;
  }

  if (suffix === 'pm' && hour !== 12) {
    hour += 12;
  } else if (suffix === 'am' && hour === 12) {
    hour = 0;
  } else if (!suffix && hour >= 1 && hour <= 6) {
    // Extracted exam times often omit AM/PM, and afternoon slots are written
    // in 12-hour-looking form. Shift only the obvious afternoon window.
    hour += 12;
  }

  return { hour, minute, suffix };
}

function parseTimeRange(rawTime) {
  const text = normalizeText(rawTime);
  if (!text) {
    return null;
  }

  const [startRaw, endRaw] = text.split('-', 2);
  if (!startRaw || !endRaw) {
    return null;
  }

  const start = parseTimeSegment(startRaw);
  const end = parseTimeSegment(endRaw);
  if (!start || !end) {
    return null;
  }

  let endHour = end.hour;
  if (!end.suffix && !start.suffix && endHour <= start.hour) {
    endHour += 12;
  }

  return {
    start: {
      hour: start.hour,
      minute: start.minute,
    },
    end: {
      hour: endHour,
      minute: end.minute,
    },
  };
}

function parseDaysOfWeek(dayValue) {
  const normalized = normalizeText(dayValue)
    .toUpperCase()
    .replace(/[^A-Z]/g, '');

  if (!normalized) {
    return [];
  }

  if (normalized.includes('MWF')) {
    return [1, 3, 5];
  }

  if (normalized.includes('TTH') || normalized.includes('TTHR')) {
    return [2, 4];
  }

  const days = [];
  let index = 0;

  while (index < normalized.length) {
    const twoCharToken = normalized.slice(index, index + 2);
    if (twoCharToken === 'TH') {
      days.push(WEEKDAY_MAP.TH);
      index += 2;
      continue;
    }

    if (twoCharToken === 'SU') {
      days.push(WEEKDAY_MAP.SU);
      index += 2;
      continue;
    }

    const token = normalized[index];
    if (WEEKDAY_MAP[token] !== undefined) {
      days.push(WEEKDAY_MAP[token]);
    }
    index += 1;
  }

  return [...new Set(days)];
}

function buildDateTime(dateValue, hour, minute) {
  return new Date(
    dateValue.getFullYear(),
    dateValue.getMonth(),
    dateValue.getDate(),
    hour,
    minute,
    0,
    0,
  );
}

export function normalizeScheduleEvents(scheduleInput) {
  const rows = flattenScheduleInput(scheduleInput);

  return rows.flatMap((row, index) => {
    if (!row || typeof row !== 'object') {
      return [];
    }

    if (isAlreadyCalendarEvent(row)) {
      return [row];
    }

    const examDayValue = row.exam_day ?? row.examDay ?? row.class_day ?? row.class_days ?? row.day;
    const examTimeValue = row.exam_time ?? row.examTime ?? row.class_time;
    const examDate = parseDateValue(examDayValue);
    const timeRange = parseTimeRange(examTimeValue);

    if (!timeRange) {
      return [];
    }

    const title = row.subject || row.course_year || row.major_exam || 'Exam';
    const id = [
      row.file_hash || row.hash || 'schedko',
      row.course_year || 'course',
      row.subject || 'subject',
      examTimeValue || 'time',
      examDayValue || 'day',
      index,
    ].join('-');

    if (examDate) {
      return [
        {
          id,
          title,
          start: buildDateTime(examDate, timeRange.start.hour, timeRange.start.minute),
          end: buildDateTime(examDate, timeRange.end.hour, timeRange.end.minute),
          extendedProps: {
            ...row,
            exam_day: examDayValue,
            exam_time: examTimeValue,
          },
        },
      ];
    }

    const daysOfWeek = parseDaysOfWeek(examDayValue);
    if (daysOfWeek.length > 0) {
      return [
        {
          id,
          title,
          daysOfWeek,
          startTime: `${String(timeRange.start.hour).padStart(2, '0')}:${String(timeRange.start.minute).padStart(2, '0')}:00`,
          endTime: `${String(timeRange.end.hour).padStart(2, '0')}:${String(timeRange.end.minute).padStart(2, '0')}:00`,
          extendedProps: {
            ...row,
            exam_day: examDayValue,
            exam_time: examTimeValue,
          },
        },
      ];
    }

    return [];
  });
}
