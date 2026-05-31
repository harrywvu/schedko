import { useRef, useState } from 'react'
import html2canvas from 'html2canvas'
import FullCalendar from '@fullcalendar/react'
import timeGridPlugin from '@fullcalendar/timegrid'
import './Calendar.css';
import { normalizeScheduleEvents } from '../utils/scheduleEvents';

function toValidDate(value) {
  if (!value) {
    return null;
  }

  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function toDayBoundaryDate(value) {
  if (!value) {
    return null;
  }

  if (value instanceof Date) {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate());
  }

  if (typeof value === 'string') {
    const dateOnlyMatch = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (dateOnlyMatch) {
      return new Date(
        Number(dateOnlyMatch[1]),
        Number(dateOnlyMatch[2]) - 1,
        Number(dateOnlyMatch[3]),
      );
    }
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function formatClock(value) {
  const date = toValidDate(value);
  if (!date) {
    return 'TBA';
  }

  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDayLabel(value) {
  const date = toDayBoundaryDate(value);
  if (!date) {
    return 'Unknown day';
  }

  return date.toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

function getCellStyle(seed) {
  const palette = [
    '#60a5fa',
    '#4ade80',
    '#f59e0b',
    '#f472b6',
    '#a78bfa',
    '#22d3ee',
    '#fb7185',
    '#f97316',
  ];

  const borderPalette = [
    '#1d4ed8',
    '#15803d',
    '#b45309',
    '#be185d',
    '#7c3aed',
    '#0891b2',
    '#be123c',
    '#ea580c',
  ];

  const key = String(seed || '');
  const hash = key.split('').reduce((acc, char) => ((acc * 31) + char.charCodeAt(0)) >>> 0, 0);
  const index = hash % palette.length;

  return {
    background: palette[index],
    border: borderPalette[index],
    text: '#000000',
  };
}

export default function Calendar({ events = [] }) {
  const calendarRef = useRef()
  const [isExporting, setIsExporting] = useState(false);

  const calendarEvents = (() => {
    try {
      return normalizeScheduleEvents(events).filter((event) => {
        if (event?.start) {
          return Boolean(toValidDate(event.start));
        }

        return Array.isArray(event?.daysOfWeek) && event.daysOfWeek.length > 0;
      });
    } catch (error) {
      console.error('Failed to prepare calendar events:', error);
      return [];
    }
  })();

  const visibleRange = (() => {
    const startDates = calendarEvents
      .map((event) => toDayBoundaryDate(event?.start))
      .filter(Boolean);

    if (startDates.length === 0) {
      return null;
    }

    const earliest = new Date(Math.min(...startDates.map((date) => date.getTime())));
    const latest = new Date(Math.max(...startDates.map((date) => date.getTime())));
    const end = new Date(latest);
    end.setDate(end.getDate() + 1);

    return {
      start: earliest,
      end,
    };
  })();

  const initialDate = visibleRange?.start || new Date();

  if (calendarEvents.length === 0) {
    return (
      <div className="calendar-root">
        <div className="calendar-wrapper">
          <p className="text-sm text-slate-600 p-4">
            No calendar events could be generated from the selected schedule.
          </p>
        </div>
      </div>
    );
  }

  const downloadImage = async () => {
    if (!calendarRef.current) {
      return;
    }

    setIsExporting(true);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    try {
      const canvas = await html2canvas(calendarRef.current, {
        backgroundColor: '#ffffff',
        scale: 2,
        useCORS: true,
        windowWidth: calendarRef.current.scrollWidth,
        windowHeight: calendarRef.current.scrollHeight,
      });

      const link = document.createElement('a')
      link.download = 'schedko-timetable.png'
      link.href = canvas.toDataURL('image/png')
      link.click()
    } finally {
      setIsExporting(false);
    }
  }

  const renderEventContent = (eventInfo) => {
    const details = eventInfo.event.extendedProps || {};
    const room = details.exam_room || details.room || 'TBA';
    const building = details.exam_building || details.building || 'TBA';
    const examiner = details.examiner || 'TBA';
    return (
      <div className="calendar-event-card">
        <div className="calendar-event-title">{eventInfo.event.title}</div>
        <div className="calendar-event-meta">{formatClock(eventInfo.event.start)} - {formatClock(eventInfo.event.end)}</div>
        <div className="calendar-event-submeta">{room} · {building}</div>
        <div className="calendar-event-submeta">Examiner: {examiner}</div>
      </div>
    );
  };

  const handleEventDidMount = (info) => {
    const style = getCellStyle(info.event.id || info.event.title || info.event.start?.toISOString());
    info.el.style.setProperty('background-color', style.background, 'important');
    info.el.style.setProperty('border-color', style.border, 'important');
    info.el.style.setProperty('color', style.text, 'important');
    info.el.style.setProperty('--event-bg', style.background);
    info.el.style.setProperty('--event-border', style.border);
    info.el.style.setProperty('--event-text', style.text);
    info.el.querySelectorAll('*').forEach((node) => {
      node.style.setProperty('color', style.text, 'important');
    });
  };

  return (
    <div className="calendar-root">
      <button className="calendar-download-btn" onClick={downloadImage}>Download as Image</button>
      {isExporting ? <div className="calendar-export-note">Preparing a clean image export...</div> : null}
      <div className="calendar-range-note">
        Showing {formatDayLabel(visibleRange?.start)} to {formatDayLabel(visibleRange ? new Date(visibleRange.end.getTime() - 86400000) : null)}
      </div>
      <div ref={calendarRef} className="calendar-wrapper">
        <FullCalendar
          headerToolbar={{
            start: '',
            center: 'title',
            end: ''
          }}
          plugins={[timeGridPlugin]}
          contentHeight="auto"
          initialView="examPeriod"
          initialDate={initialDate}
          views={{
            examPeriod: {
              type: 'timeGrid',
              visibleRange: visibleRange || undefined,
            },
          }}
          firstDay={1}
          weekends={false}
          slotMinTime="07:00:00"
          slotMaxTime="18:00:00"
          hiddenDays={[0, 6]}
          allDaySlot={false}
          slotDuration="00:20:00"
          events={calendarEvents}
          eventContent={renderEventContent}
          eventDidMount={handleEventDidMount}
        />
      </div>
    </div>
  )
}
