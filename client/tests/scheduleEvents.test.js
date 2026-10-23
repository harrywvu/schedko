import assert from 'node:assert/strict';
import { test } from 'node:test';
import { normalizeScheduleEvents } from '../src/utils/scheduleEvents.js';

test('unknown exam time does not become a class-time exam', () => {
  assert.deepEqual(normalizeScheduleEvents([{
    subject: 'TEST', exam_day: '2026-05-07', exam_time: null, class_time: '8:00-9:00 AM',
  }]), []);
});

test('unknown exam day does not become a recurring class-day exam', () => {
  assert.deepEqual(normalizeScheduleEvents([{
    subject: 'TEST', exam_day: null, exam_time: '8:00-9:00 AM', class_days: 'MWF',
  }]), []);
});

test('known exam values produce an event while legacy aliases still work', () => {
  const events = normalizeScheduleEvents([
    { subject: 'TEST', exam_day: '2026-05-07', exam_time: '9:00-10:00 AM' },
    { subject: 'LEGACY', examDay: '2026-05-08', examTime: '9:00-10:00 AM' },
  ]);
  assert.equal(events.length, 2);
  assert.equal(events[0].title, 'TEST');
  assert.equal(events[1].title, 'LEGACY');
});
