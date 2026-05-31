import { useRef } from 'react'
import html2canvas from 'html2canvas'
import FullCalendar from '@fullcalendar/react'
import timeGridPlugin from '@fullcalendar/timegrid'
import './Calendar.css';
import { normalizeScheduleEvents } from '../utils/scheduleEvents';

export default function Calendar({ events = [] }) {
  const calendarRef = useRef()

  const calendarEvents = normalizeScheduleEvents(events);
  const initialDate = (() => {
    const firstTimedEvent = calendarEvents.find((event) => event.start);
    return firstTimedEvent?.start ? new Date(firstTimedEvent.start) : new Date();
  })();

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
          initialView="timeGridWeek"
          initialDate={initialDate}
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
