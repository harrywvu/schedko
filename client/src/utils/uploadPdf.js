export async function uploadPdf(url, file) {
  const formData = new FormData();
  formData.append('file', file);

  // Upload checks are idempotent; retry a stalled cold-start connection in place.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new DOMException('Upload timed out', 'TimeoutError')), 30_000);
    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      });
      if (!response.ok) {
        if ([502, 503, 504].includes(response.status) && attempt < 2) continue;
        throw new Error(`Upload failed (HTTP ${response.status}). Please try again.`);
      }
      return await response.json();
    } catch (error) {
      if (attempt === 2 || !(error instanceof TypeError || error.name === 'TimeoutError')) throw error;
    } finally {
      clearTimeout(timer);
    }
  }
}
