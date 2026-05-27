import { useRef } from 'react'
import html2canvas from 'html2canvas'
import FullCalendar from '@fullcalendar/react'
import timeGridPlugin from '@fullcalendar/timegrid'
import './Calendar.css';

export default function Calendar({ events = [] }) {
  const calendarRef = useRef()

  // Use passed-in events if provided, else fallback to myArray
  const calendarEvents = events;

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
          titleFormat={() => 'Finals Exam'}
          plugins={[timeGridPlugin]}
          contentHeight="auto"
          initialView="timeGridWeek"
          initialDate={calendarEvents[0]?.start?.slice(0, 10) || "2025-05-28"}
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
