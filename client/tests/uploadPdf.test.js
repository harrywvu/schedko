import assert from 'node:assert/strict';
import { test } from 'node:test';
import { uploadPdf } from '../src/utils/uploadPdf.js';

const pdf = new Blob(['%PDF-1.4 synthetic test file'], { type: 'application/pdf' });

test('first upload recovers from a stalled cold-start request without a reload', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let firstSignal;
  const fetchMock = t.mock.method(globalThis, 'fetch', async (_url, { signal }) => {
    if (!firstSignal) {
      firstSignal = signal;
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    }
    return Response.json({ hash: 'new-pdf', status: 'new' });
  });
  const result = uploadPdf('/upload', pdf);
  t.mock.timers.tick(30_000);
  assert.deepEqual(await result, { hash: 'new-pdf', status: 'new' });
  assert.equal(firstSignal.aborted, true);
  assert.equal(fetchMock.mock.callCount(), 2);
  for (const call of fetchMock.mock.calls) {
    assert.equal(await call.arguments[1].body.get('file').text(), await pdf.text());
  }
});

test('cached uploads return immediately', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => Response.json({ hash: 'cached', status: 'cached' }));
  assert.equal((await uploadPdf('/upload', pdf)).status, 'cached');
  assert.equal(fetchMock.mock.callCount(), 1);
});

test('retries transient network and gateway errors', async (t) => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls += 1;
    if (calls === 1) throw new TypeError('Failed to fetch');
    if (calls === 2) return new Response('', { status: 503 });
    return Response.json({ hash: 'active', status: 'processing' });
  });
  assert.equal((await uploadPdf('/upload', pdf)).status, 'processing');
  assert.equal(calls, 3);
});

test('stops after three stalled requests so the UI can display an error', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const fetchMock = t.mock.method(globalThis, 'fetch', (_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }));
  const result = assert.rejects(uploadPdf('/upload', pdf), { name: 'TimeoutError' });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    t.mock.timers.tick(30_000);
    await Promise.resolve();
    await Promise.resolve();
  }
  await result;
  assert.equal(fetchMock.mock.callCount(), 3);
});

test('does not retry validation failures', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 400 }));
  await assert.rejects(uploadPdf('/upload', pdf), /HTTP 400/);
  assert.equal(fetchMock.mock.callCount(), 1);
});
