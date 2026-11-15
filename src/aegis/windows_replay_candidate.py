"""Experimental restart-aware synthetic polling, never live equipment telemetry."""
import asyncio
from datetime import datetime, timezone
from .adapter_input import SyntheticReplayAdapter, AdapterError, AdapterReadRequest, validate_page
from .windows_adapter_candidate import WindowsAdapterCandidate


class WindowsReplaySessionCandidate:
    def __init__(self, adapter, store, *, timeout_ms=100, max_attempts=1, utc_now=None):
        if type(adapter) is not SyntheticReplayAdapter or type(store) is not WindowsAdapterCandidate:
            raise AdapterError('UNSUPPORTED_ADAPTER')
        if (type(timeout_ms) is not int or not 1 <= timeout_ms <= 10000
                or type(max_attempts) is not int or not 1 <= max_attempts <= 3):
            raise AdapterError('INVALID_READ_POLICY')
        if adapter.fixture_wire != store.fixture_wire:
            raise AdapterError('STORE_BINDING_MISMATCH')
        self.adapter, self.store = adapter, store
        self._timeout = timeout_ms / 1000
        self._max_attempts = max_attempts
        self._now = utc_now or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._last_error = None

    def state_view(self):
        result = self.store.state_view()
        result['lastReadError'] = self._last_error
        return result

    async def _read(self, request):
        for attempt in range(self._max_attempts):
            try:
                async with asyncio.timeout(self._timeout):
                    return await self.adapter.read(request)
            except asyncio.CancelledError:
                self._last_error = 'READ_CANCELLED'
                raise
            except TimeoutError as error:
                self._last_error = 'ADAPTER_TIMEOUT'
                if attempt + 1 == self._max_attempts:
                    raise AdapterError('ADAPTER_TIMEOUT') from error
            except Exception:
                self._last_error = 'READ_OR_COMMIT_FAILED'
                raise

    async def poll_once(self):
        async with self._lock:
            state = self.store.state_view()
            if state['ended']:
                return {'status': 'END_OF_SYNTHETIC_INPUT', 'sourceAcknowledgement': 'NOT_SENT'}
            request = AdapterReadRequest(state['committedCursor'])
            payload = await self._read(request)
            # Persistence is deliberately outside the retry loop. An uncertain write
            # must fence the store, never trigger a second read or automatic append.
            try:
                if validate_page(payload).model.afterToken != request.after_token:
                    raise AdapterError('RESPONSE_CURSOR_MISMATCH')
                result = self.store.commit_page(payload, self._now())
                self._last_error = None
                return result
            except Exception:
                self._last_error = 'READ_OR_COMMIT_FAILED'
                raise
