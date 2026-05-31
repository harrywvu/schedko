import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import Calendar from './Calendar';
import './Results.css';
import { normalizeScheduleEvents } from '../utils/scheduleEvents';

function toDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date : null;
}

function formatDay(value, fallback = 'Unknown day') {
  const date = toDate(value);
  if (!date) {
    return fallback;
  }

  return date.toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

const Results = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const events = React.useMemo(() => {
    try {
      return normalizeScheduleEvents(location.state?.events || location.state?.dbSchedules || []);
    } catch (error) {
      console.error('Failed to normalize schedule events:', error);
      return [];
    }
  }, [location.state?.events, location.state?.dbSchedules]);

  if (!location.state || events.length === 0) {
    return (
      <div className="results-page">
        <div className="results-container results-empty">
          <p className="results-kicker">Exam Schedule Results</p>
          <h1 className="results-title">No schedule data found.</h1>
          <p className="results-intro">
            This can happen if you refresh the page or open the results route directly. Return to
            the home page and upload your file again.
          </p>
          <button className="results-btn" onClick={() => navigate('/')}>
            Back to Home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="results-page">
      <div className="results-container">
        <div className="results-header">
          <div>
            <p className="results-kicker">Exam Schedule Results</p>
            <h1 className="results-title">
              {events.length} exam{events.length !== 1 ? 's' : ''} found
            </h1>
            <p className="results-intro">
              Review the schedule below, then compare it against the calendar timeline for the exam
              period.
            </p>
          </div>
          <div className="results-summary-card">
            <span className="summary-label">Total exams</span>
            <span className="summary-value">{events.length}</span>
          </div>
        </div>

        <section className="results-table-card">
          <div className="section-heading">
            <h2>Schedule overview</h2>
          </div>
          <div className="table-scroll">
            <table className="results-table">
              <thead>
                <tr>
                  <th>Course</th>
                  <th>Date</th>
                  <th>Time</th>
                  <th>Examiner</th>
                  <th>Room</th>
                  <th>Building</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, index) => {
                  const details = event.extendedProps || {};
                  const startDate = event.start ? new Date(event.start) : null;
                  const endDate = event.end ? new Date(event.end) : null;
                  const dayLabel = startDate
                    ? formatDay(startDate)
                    : Array.isArray(event.daysOfWeek)
                      ? event.daysOfWeek.map((day) => ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][day]).join(', ')
                      : (details.exam_day || 'Unknown day');
                  const examTimeLabel = startDate && endDate
                    ? `${startDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} - ${endDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
                    : (details.exam_time || `${event.startTime || 'Unknown'} - ${event.endTime || 'Unknown'}`);

                  return (
                    <tr key={event.id || `${event.title}-${index}`}>
                      <td>
                        <div className="course-cell">
                          <span className="course-title">{event.title}</span>
                          <span className="course-subtitle">{details.course_year || details.subject || 'Exam entry'}</span>
                        </div>
                      </td>
                      <td>{dayLabel}</td>
                      <td>{examTimeLabel}</td>
                      <td>{details.examiner || 'TBA'}</td>
                      <td>{details.exam_room || details.room || 'TBA'}</td>
                      <td>{details.exam_building || details.building || 'TBA'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="results-calendar-wrapper">
          <Calendar events={events} />
        </section>

        <button className="results-btn" onClick={() => navigate('/')}>
          Back to Home
        </button>
      </div>
    </div>
  );
};

export default Results;
