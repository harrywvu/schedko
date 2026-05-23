import React, { useEffect, useState } from 'react';
import { mockSchedules } from '../data/mockSchedules';

function groupByClassCode(schedules) {
  const grouped = {};
  for (const sched of schedules) {
    if (!grouped[sched.classCode]) grouped[sched.classCode] = [];
    grouped[sched.classCode].push(sched);
  }
  return grouped;
}

const Database = () => {
  const [grouped, setGrouped] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setGrouped(groupByClassCode(mockSchedules));
    setLoading(false);
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center font-jost text-lg text-green-900">
        Loading schedules...
      </div>
    );
  }
  
  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <h2 className="font-lora text-xl text-red-800 mb-2">Error Loading Schedules</h2>
          <p className="font-jost text-red-700">{error}</p>
          <button 
            onClick={() => window.location.reload()} 
            className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const hasSchedules = Object.keys(grouped).length > 0;

  if (!hasSchedules) {
    return (
      <div className="min-h-screen bg-gray-50 py-8">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <h1 className="font-lora text-3xl font-bold text-green-900 mb-8">Database</h1>
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6">
            <p className="font-jost text-yellow-800">No schedules found in the database.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <h1 className="font-lora text-3xl font-bold text-green-900 mb-8">Database</h1>
        <div className="grid gap-8 grid-cols-1 sm:grid-cols-2 md:grid-cols-3">
          {Object.entries(grouped).map(([classCode, schedules]) => (
            <div key={classCode} className="bg-white rounded-xl shadow p-6 border-t-4 border-green-600">
              <h2 className="font-lora text-xl text-green-800 mb-4">{classCode}</h2>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm font-jost">
                  <thead>
                    <tr className="bg-green-50">
                      <th className="px-2 py-1 text-left text-green-900">Course</th>
                      <th className="px-2 py-1 text-left text-green-900">Day</th>
                      <th className="px-2 py-1 text-left text-green-900">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {schedules.map((sched, idx) => (
                      <tr key={idx} className="border-b last:border-b-0">
                        <td className="px-2 py-1">{sched.course}</td>
                        <td className="px-2 py-1">{sched.date || sched.day}</td>
                        <td className="px-2 py-1">{sched.time}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Database;