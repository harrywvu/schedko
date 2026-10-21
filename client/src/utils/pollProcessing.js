const POLL_INTERVAL_MS = 2_000;
const REQUEST_TIMEOUT_MS = 15_000;
const MAX_FAILURES = 5;

function retryAfterMs(value) {
  if (!value) return 0;
  const seconds = Number(value);
  const delay = Number.isFinite(seconds) ? seconds * 1_000 : Date.parse(value) - Date.now();
  return Number.isFinite(delay) ? Math.max(0, delay) : 0;
}

export function startProcessingPolling(url, { onProgress, onUnavailable, onRetry }) {
  let stopped = false;
  let timer;
  let controller;
  let failures = 0;

  const poll = async () => {
    controller = new AbortController();
    const requestTimer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let delay = POLL_INTERVAL_MS;

    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) {
        const error = new Error('Processing status unavailable');
        error.status = response.status;
        error.retryAfter = retryAfterMs(response.headers.get('Retry-After'));
        throw error;
      }

      const data = await response.json();
      if (stopped) return;
      failures = 0;
      onProgress(data);
      if (['ready', 'failed', 'cancelled'].includes(data.status)) return;
    } catch (error) {
      if (stopped) return;
      failures += 1;
      const permanentError = error.status >= 400 && error.status < 500 && error.status !== 429;
      if (permanentError || failures >= MAX_FAILURES) {
        onUnavailable('Unable to check processing progress. The server may be unavailable. Please try again.');
        return;
      }
      delay = Math.max(Math.min(POLL_INTERVAL_MS * 2 ** failures, 30_000), error.retryAfter || 0);
      onRetry?.(delay);
    } finally {
      clearTimeout(requestTimer);
    }

    if (!stopped) timer = setTimeout(poll, delay);
  };

  void poll();

  return () => {
    stopped = true;
    clearTimeout(timer);
    controller?.abort();
  };
}
