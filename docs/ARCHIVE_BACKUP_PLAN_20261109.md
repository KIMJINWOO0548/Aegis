# Archive backup first step

Base: `52844968bbd25ea6ea14bea40ca64b3b37a86fbe`. This is independent accessible history, not restoration of unavailable preservation copies.

Implement an offline backend CLI only. Existing collector, writer, schema, API and UI remain unchanged. Use SQLite online backup from a read-only, pinned WAL snapshot. Validate schema v1 and raw1.1 rows; compare original JSON text, first receipt, hierarchy, active-run cursors and recorded gaps with deterministic table digests. Sparse and retired runs are supported; source completeness is not asserted.

Backup and restore create a new file only. Refuse existing destination/companions and source companion names. Build in a private sibling directory; close into standalone DELETE journal form, validate and fsync before atomic no-replace publication. Clean only staging names created by this invocation; never delete source, existing files or completed backups. After publication, failures preserve the published file and explicitly report an uncertain outcome.

The unsigned stdout manifest binds the completed file SHA256 and logical snapshot. Verify and restore require this operator-retained reference. Restore validates into a fresh file; it does not activate or replace a live archive. Physical hashes may differ after SQLite copies, so restored logical content must match the reference and the new file receives its own SHA256.

Bound database pages, table rows, payload sizes, manifest bytes and elapsed/SQL work. These are cooperative limits, not hard real-time I/O interruption. Operate only in a trusted local directory without concurrent path manipulation. Windows, remote filesystems, physical devices, power loss and long-duration load remain unverified.

Test first: actual CLI roundtrip and failure paths; pinned snapshot while WAL writer commits raw/cursor/gap; uncommitted exclusion; cursor/gap/payload inconsistency; corruption and manifest mismatch; destination/race protection; injected copy, validation, capacity and fsync errors; cooperative limits and connection release. Then full suite, emulator/contracts, independent review, privacy/noreply checks, normal separate-branch push and remote SHA verification.
