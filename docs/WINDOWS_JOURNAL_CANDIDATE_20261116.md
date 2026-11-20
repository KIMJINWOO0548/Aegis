# Windows receipt journal candidate

This is an experimental **separate backend primitive**, not an AdapterStore or
nullable archive migration. Production storage/backup/preflight gates are intact.

`aegis.windows_journal_candidate.WindowsJournalCandidate` provides append, close,
reopen and exact record reconstruction in one bounded file. It uses a native
exclusive handle, CREATE_NEW for new files, final-component no-follow,
regular-file/single-link checks, write-through, FlushFileBuffers and exact readback.
There are no SQLite WAL/SHM/journal companion files to redirect. No caller file is
overwritten, renamed, automatically repaired or deleted. Same-object calls are
serialized by a reentrant lock; another process cannot open the active file.

Each frame has a bounded length and chained SHA256. Invalid header, corruption or
partial tail rejects reopening. An append error after I/O starts fences the object
as RESULT_UNKNOWN; a new open determines whether a complete record is present.
Successful append reports LOCAL_FLUSH_CONFIRMED and sourceAcknowledgement NOT_SENT.
That status intentionally does not claim power-cut durability, durable directory
publication or delivery confirmation. Flush/close behavior depends on the device.

Limits: 1 MiB per nonempty byte record, 16 MiB total. This small candidate rereads
and validates the bounded file on each append. It is not a high-rate collector.
Application record schema, cursor transaction semantics, delivery deduplication,
indexing, migration and a production writer process remain to be implemented.

Requires a trusted local fixed-disk directory. Parent reparse redirection and
already existing writable mappings are not excluded; there is no sandbox/hostile
directory claim. Coherent truncation of full records or coordinated replacement
by the owner is not detected without an independent trusted anchor. Hashes are
integrity checks, not signatures. A torn tail is preserved for explicit recovery,
not silently truncated, and can currently block access to the earlier prefix.

## Actual Windows tests

`PYTHONPATH=src python -m unittest discover -s tests -p test_windows_journal_candidate.py -v`

On Windows with Python 3.12.10 the final suite passed **14/14**. An earlier
11-test version also passed three consecutive runs. Independent review found a
flush+close double-failure exception masking issue; the final version preserves
RESULT_UNKNOWN and keeps handle ownership until close succeeds. The context
manager also preserves the active error if its second cleanup attempt fails.
Three new regression tests cover these paths. This is not a full Aegis regression result.
Independent review reran the final 14 tests successfully and verified both
exception-preservation fixes. It found no further blocker within this experimental
scope; this is not approval to enable the production storage backend.

- commit/close/reopen/resume;
- exclusive open, no replacement and rename denial;
- corruption and partial tail rejection without modification;
- flush failure -> fenced unknown -> reopen authoritative data;
- injected partial disk write -> preserved torn tail and recovery required;
- hardlink rejection and foreign file unchanged;
- own child process killed after confirmed append -> reopen/resume;
- eight threads / forty appends on one instance serialized;
- invalid and oversized records rejected without writes.

Actual disk exhaustion, process termination at every write boundary, power loss,
symlink/junction adversaries and native crash telemetry are NOT_RUN. No existing
runtime data or service settings were used. Backup publishing is not implemented.
