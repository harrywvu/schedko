import { useRef } from 'react'
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

export default function Calendar({ events = [] }) {
  const calendarRef = useRef()

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

  const downloadImage = () => {
    html2canvas(calendarRef.current).then(canvas => {
      const link = document.createElement('a')
      link.download = 'calendar.png'
      link.href = canvas.toDataURL()
      link.click()
    })
  }

  return (
    <div className="calendar-root">
      <button className="calendar-download-btn" onClick={downloadImage}>Download as Image</button>
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
        />
      </div>
    </div>
  )
}
