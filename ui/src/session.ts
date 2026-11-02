export async function readSession(signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<'ready' | 'locked'> {
  signal.throwIfAborted();
  const response = await fetcher('/api/session', {
    method: 'GET', credentials: 'same-origin', cache: 'no-store', signal,
  });
  signal.throwIfAborted();
  if (response.status === 401) return 'locked';
  if (!response.ok) throw Error('SESSION_UNAVAILABLE');
  return 'ready';
}
