import {describe, expect, it, vi} from 'vitest';
import {readSession} from './session';

describe('read-only local session recovery', () => {
  it('reuses same-origin credentials with a GET and never sends a token body', async () => {
    const controller = new AbortController();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', {status: 200}));
    await expect(readSession(controller.signal, fetcher)).resolves.toBe('ready');
    expect(fetcher).toHaveBeenCalledExactlyOnceWith('/api/session', {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
    });
  });
  it('keeps a 401 locked so the user must submit a token explicitly', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', {status: 401}));
    await expect(readSession(new AbortController().signal, fetcher)).resolves.toBe('locked');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([403, 500, 503])('reports HTTP %s as unavailable rather than authenticated', async status => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{}', {status}));
    await expect(readSession(new AbortController().signal, fetcher)).rejects.toThrow('SESSION_UNAVAILABLE');
  });
  it('does not turn a network failure into an authenticated session', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Network unavailable'));
    await expect(readSession(new AbortController().signal, fetcher)).rejects.toThrow('Network unavailable');
  });
  it('does not start a request after its owner has aborted', async () => {
    const controller = new AbortController(); controller.abort();
    const fetcher = vi.fn<typeof fetch>();
    await expect(readSession(controller.signal, fetcher)).rejects.toMatchObject({name: 'AbortError'});
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('rejects late success after cancellation even if the fetch adapter resolves', async () => {
    const controller = new AbortController();
    let finish!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise(resolve => {finish = resolve;}));
    const pending = readSession(controller.signal, fetcher);
    controller.abort(); finish(new Response('{}', {status: 200}));
    await expect(pending).rejects.toMatchObject({name: 'AbortError'});
  });
});
