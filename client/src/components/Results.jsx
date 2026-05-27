import React from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import Calendar from './Calendar';
import './Results.css'; // Add a CSS file for Results-specific styles


const Results = () => {
  const location = useLocation();
  const navigate = useNavigate();
  
  const events = location.state?.events || [];

  // Defensive: If location.state is missing or no events, show error
  if (!location.state || events.length === 0) {
    return (
      <div className="results-container">
        <h1 className="results-title">Exam Schedule Results</h1>
        <div className="results-error">
          <h3 className="font-bold">No Schedule Data Found</h3>
          <p className="mt-2">
            No schedule data was provided. This can happen if you refreshed the page or navigated here directly.<br />
            Please return to the home page and upload or select your schedule again.
          </p>
        </div>
        <button
          className="results-btn"
          onClick={() => navigate('/')}
        >
          Back to Home
        </button>
      </div>
    );
  }

  React.useEffect(() => {
    console.log('location.state:', location.state);
  }, [location.state]);

  return (
    <div className="results-container">
      <h1 className="results-title">
        Exam Schedule Results ({events.length} exam{events.length !== 1 ? 's' : ''})
      </h1>
      <div className="results-details">
        <h3 className="font-semibold mb-2">Schedule Details:</h3>
        {events.map((event, index) => {
          const details = event.extendedProps || {};
          const dateLabel = event.start ? new Date(event.start).toLocaleDateString() : 'Unknown date';
          const examTimeLabel = details.examTime || `${new Date(event.start).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})} - ${new Date(event.end).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}`;

          return (
          <div key={event.id || `${event.title}-${index}`} className="results-detail-item">
            <strong>{event.title}</strong> - {dateLabel} {examTimeLabel} - 
            Examiner {details.examiner || 'TBA'} - Room {details.room || 'TBA'} - {details.building || 'TBA'}
          </div>
          );
        })}
      </div>
      <div className="results-calendar-wrapper">
        <Calendar events={events} />
      </div>
      <button
        className="results-btn mt-6"
        onClick={() => navigate('/')}
      >
        Back to Home
      </button>
    </div>
  );
};

export default Results;