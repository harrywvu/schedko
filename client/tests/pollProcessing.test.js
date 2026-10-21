import assert from 'node:assert/strict';
import { test } from 'node:test';
import { startProcessingPolling } from '../src/utils/pollProcessing.js';

async function flush() {
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
}

function setup(t, fetchHandler) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const fetchMock = t.mock.method(globalThis, 'fetch', fetchHandler);
  const progress = t.mock.fn();
  const unavailable = t.mock.fn();
  const retry = t.mock.fn();
  const stop = startProcessingPolling('/processing/test-hash', {
    onProgress: progress,
    onUnavailable: unavailable,
    onRetry: retry,
  });
  t.after(stop);
  return { fetchMock, progress, unavailable, retry, stop };
}

test('waits for each response before scheduling another request', async (t) => {
  let resolve;
  let calls = 0;
  const { fetchMock, progress } = setup(t, () => {
    calls += 1;
    return calls === 1
      ? new Promise((done) => { resolve = done; })
      : Promise.resolve(Response.json({ status: 'ready' }));
  });
  t.mock.timers.tick(5_000);
  assert.equal(fetchMock.mock.callCount(), 1);
  resolve(Response.json({ status: 'processing' }));
  await flush();
  t.mock.timers.tick(1_999);
  assert.equal(fetchMock.mock.callCount(), 1);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(fetchMock.mock.callCount(), 2);
  assert.equal(progress.mock.calls.at(-1).arguments[0].status, 'ready');
  t.mock.timers.tick(60_000);
  assert.equal(fetchMock.mock.callCount(), 2);
});

test('honors Retry-After on 429 and resumes when the server recovers', async (t) => {
  let calls = 0;
  const { fetchMock, retry, progress } = setup(t, async () => {
    calls += 1;
    return calls === 1
      ? new Response('', { status: 429, headers: { 'Retry-After': '30' } })
      : Response.json({ status: 'ready' });
  });
  await flush();
  assert.equal(retry.mock.calls[0].arguments[0], 30_000);
  t.mock.timers.tick(29_999);
  assert.equal(fetchMock.mock.callCount(), 1);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(fetchMock.mock.callCount(), 2);
  assert.equal(progress.mock.calls[0].arguments[0].status, 'ready');
});

test('backs off and stops after five consecutive gateway failures', async (t) => {
  const { fetchMock, unavailable } = setup(t, async () => new Response('', { status: 503 }));
  await flush();
  for (const delay of [4_000, 8_000, 16_000, 30_000]) {
    t.mock.timers.tick(delay);
    await flush();
  }
  assert.equal(fetchMock.mock.callCount(), 5);
  assert.equal(unavailable.mock.callCount(), 1);
  t.mock.timers.tick(120_000);
  assert.equal(fetchMock.mock.callCount(), 5);
});

test('times out a stalled request before retrying', async (t) => {
  let signal;
  const { fetchMock, retry } = setup(t, (_url, options) => {
    signal = options.signal;
    return new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
  });
  t.mock.timers.tick(15_000);
  await flush();
  assert.equal(signal.aborted, true);
  assert.equal(retry.mock.callCount(), 1);
  assert.equal(fetchMock.mock.callCount(), 1);
  t.mock.timers.tick(4_000);
  assert.equal(fetchMock.mock.callCount(), 2);
});

test('cleanup ignores late responses and prevents future polling', async (t) => {
  let resolve;
  const { fetchMock, progress, stop } = setup(t, () => new Promise((done) => { resolve = done; }));
  stop();
  resolve(Response.json({ status: 'processing' }));
  await flush();
  t.mock.timers.tick(60_000);
  assert.equal(progress.mock.callCount(), 0);
  assert.equal(fetchMock.mock.callCount(), 1);
});

test('stops when the processing job no longer exists', async (t) => {
  const { fetchMock, unavailable } = setup(t, async () => new Response('', { status: 404 }));
  await flush();
  t.mock.timers.tick(60_000);
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(unavailable.mock.callCount(), 1);
});

test('stops on a failed job and preserves its failure message', async (t) => {
  const { fetchMock, progress } = setup(t, async () => Response.json({
    status: 'failed', message: 'Processing was interrupted.',
  }));
  await flush();
  t.mock.timers.tick(60_000);
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(progress.mock.calls[0].arguments[0].message, 'Processing was interrupted.');
});
