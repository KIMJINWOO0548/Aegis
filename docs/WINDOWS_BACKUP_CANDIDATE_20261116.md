# Windows backup publication candidate

This stage adds `prepare_backup`, `publish_backup`, and `inspect_publication` for
experimental adapter snapshots only. Production backup and platform guards stay
unchanged. Requires a trusted local directory with no concurrent path mutation.

Preparation validates and flushes a single complete snapshot under an exclusive
new staging name. Its immutable in-memory ticket binds staging, destination and
canonical snapshot SHA256. Publication accepts distinct names in the same
normalized directory, revalidates the stage, closes it, then calls MoveFileExW
with WRITE_THROUGH only. REPLACE_EXISTING, COPY_ALLOWED and delayed reboot flags
are deliberately absent. The destination is reopened and its schema/frame count
and digest checked. No caller destination is overwritten. No failure cleanup
removes either path, and no automatic retry can discard ambiguous evidence.

Any error after attempting rename is PUBLICATION_RESULT_UNKNOWN, including an
existing destination or post-move readback failure. The inspection operation
observes stage/destination as MATCH, ABSENT or UNVERIFIED. Only absent stage plus
matching destination is PUBLISHED_OBSERVED; all other combinations need review.
This observation is not a durable receipt or permission to overwrite/retry.
Malformed application snapshots are UNVERIFIED, never silently accepted.

## API evidence and limits

Microsoft's [MoveFileExW documentation](https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-movefileexw)
describes replacement as an explicit flag and cross-volume copy as another
option. Its WRITE_THROUGH explanation specifically discusses flushing copy/delete
moves. This same-directory rename implementation does **not** infer filesystem
metadata persistence or power-cut safety from that wording. Return metadata
continues to say durablePublication NOT_VERIFIED even after successful readback.

There is a deliberate closed-handle interval before pathname rename. Trusted
parent assumptions are essential: hostile stage replacement/parent reparse races
and persistent writable mappings are not solved. Post-move validation can detect
a changed payload but cannot turn a hostile-path operation into a safe one.
The ticket is not a signature, persistent external anchor or crash transaction
log. The caller must preserve its expected digest separately for later inspection.
No power-loss, disk-full, all-boundary process crash or hostile-directory test has
been completed. Production publication remains BLOCKED.

## Actual tests

Python 3.12.10: new publication tests 7/7 PASS. Combined snapshot/journal/publication
run 28 PASS / 1 FAIL. Failure is the existing journal own-child termination test:
reopening immediately after process wait raised WinError 32 (sharing violation).
An earlier 28-test run likewise produced 27 PASS / 1 FAIL at that point. The failure
was not suppressed or reclassified as success. Root cause remains unconfirmed.

Publication tests cover successful move/reopen and inspection, pre-existing
contending destination preservation, injected post-rename failure, final-readback
failure without cleanup, hash/path validation before move, exclusive-stage sharing
violation, and malformed application schema inspection. These are synthetic local
files only; existing user data and service settings were untouched.

Follow-up diagnosis identified the sharing failure's test harness cause: a Windows
venv subprocess launcher PID (31308) differed from its worker PID (26568). Killing
only the launcher left the worker holding the journal. The crash test now launches
the same Python's base executable and asserts worker-reported PID equals the owned
Popen PID before termination. No storage guard or failure check was relaxed.
After this correction the candidate suites passed **29/29** on Python 3.12.10.
Independent publication review reran **7/7 PASS** and found no blocker within the
explicit experimental scope. Historical failed runs above remain recorded.
