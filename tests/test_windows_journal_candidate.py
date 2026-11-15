import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from aegis.windows_journal_candidate import WindowsJournalCandidate as Journal, JournalError


@unittest.skipUnless(os.name == 'nt', 'Windows native handles required')
class WindowsJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aegis-journal-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'journal.bin'

    def test_commit_reopen_resume(self):
        with Journal(self.path, create=True) as journal:
            self.assertEqual(journal.append(b'original')['status'], 'LOCAL_FLUSH_CONFIRMED')
        with Journal(self.path) as journal:
            self.assertEqual(journal.records, (b'original',))
            self.assertEqual(journal.append(b'next')['sequence'], 2)
        with Journal(self.path) as journal:
            self.assertEqual(journal.records, (b'original', b'next'))

    def test_no_replace_and_exclusive_lease(self):
        with Journal(self.path, create=True) as journal:
            journal.append(b'preserve')
            for create in (False, True):
                with self.assertRaises(OSError):
                    Journal(self.path, create=create)
            with self.assertRaises(OSError):
                self.path.rename(self.path.with_suffix('.moved'))
        before = self.path.read_bytes()
        with self.assertRaises(OSError):
            Journal(self.path, create=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_tail_never_automatically_removed(self):
        with Journal(self.path, create=True) as journal:
            journal.append(b'keep')
        with self.path.open('ab') as stream:
            stream.write(b'partial')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(JournalError, 'RECOVERY_REQUIRED'):
            Journal(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corruption_rejected(self):
        with Journal(self.path, create=True) as journal:
            journal.append(b'value')
        data = bytearray(self.path.read_bytes())
        data[-1] ^= 1
        self.path.write_bytes(data)
        with self.assertRaisesRegex(JournalError, 'INTEGRITY_ERROR'):
            Journal(self.path)

    def test_flush_failure_fences_and_reopen_decides(self):
        with Journal(self.path, create=True) as journal:
            with patch.object(journal._file, 'flush', side_effect=OSError('injected')):
                with self.assertRaisesRegex(JournalError, 'RESULT_UNKNOWN'):
                    journal.append(b'possibly committed')
            with self.assertRaisesRegex(JournalError, 'RESULT_UNKNOWN'):
                _ = journal.records
        with Journal(self.path) as recovered:
            self.assertEqual(recovered.records, (b'possibly committed',))

    def test_hardlink_rejected(self):
        with Journal(self.path, create=True):
            pass
        os.link(self.path, self.path.with_suffix('.alias'))
        with self.assertRaisesRegex(JournalError, 'UNSAFE_FILE'):
            Journal(self.path)

    def test_own_process_termination_after_flush(self):
        ready = self.path.with_suffix('.ready')
        code = ('import sys,time;from pathlib import Path;'
                'from aegis.windows_journal_candidate import WindowsJournalCandidate as J;'
                'j=J(sys.argv[1],create=True);j.append(b"committed");'
                'Path(sys.argv[2]).write_text("ready");time.sleep(90)')
        p = subprocess.Popen([sys.executable, '-c', code, str(self.path), str(ready)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            until = time.monotonic() + 15
            while not ready.exists():
                self.assertIsNone(p.poll())
                self.assertLess(time.monotonic(), until)
                time.sleep(.02)
            p.kill()
            p.wait(timeout=15)
            with Journal(self.path) as journal:
                self.assertEqual(journal.records, (b'committed',))
                journal.append(b'resumed')
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=15)

    def test_invalid_append_does_not_change_data(self):
        with Journal(self.path, create=True) as journal:
            for value in (b'', 'text', b'x' * (1024 * 1024 + 1)):
                with self.assertRaisesRegex(JournalError, 'INVALID_RECORD'):
                    journal.append(value)
            self.assertEqual(journal.records, ())

    def test_same_instance_threads_are_serialized(self):
        with Journal(self.path, create=True) as journal:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(journal.append, [str(i).encode() for i in range(40)]))
            self.assertEqual(sorted(r['sequence'] for r in results), list(range(1, 41)))
            self.assertEqual(set(journal.records), {str(i).encode() for i in range(40)})
        with Journal(self.path) as reopened:
            self.assertEqual(len(reopened.records), 40)

    def test_existing_foreign_file_is_unchanged(self):
        self.path.write_bytes(b'not our journal')
        with self.assertRaisesRegex(JournalError, 'INVALID_HEADER'):
            Journal(self.path)
        self.assertEqual(self.path.read_bytes(), b'not our journal')

    def test_partial_write_fences_and_preserves_torn_tail(self):
        with Journal(self.path, create=True) as journal:
            journal.append(b'prefix')
            original = journal._file.write
            def fail_after_partial(frame):
                original(frame[:8])
                raise OSError('injected short disk write')
            with patch.object(journal._file, 'write', side_effect=fail_after_partial):
                with self.assertRaisesRegex(JournalError, 'RESULT_UNKNOWN'):
                    journal.append(b'uncertain')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(JournalError, 'RECOVERY_REQUIRED'):
            Journal(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_flush_and_close_failure_preserves_unknown_and_ownership(self):
        journal = Journal(self.path, create=True)
        self.addCleanup(journal.close)
        file = journal._file
        with patch.object(file, 'flush', side_effect=OSError('flush uncertainty')), \
                patch.object(file, 'close', side_effect=OSError('close uncertainty')):
            with self.assertRaisesRegex(JournalError, 'RESULT_UNKNOWN'):
                journal.append(b'uncertain')
            self.assertIs(journal._file, file)
            with self.assertRaisesRegex(JournalError, 'RESULT_UNKNOWN'):
                _ = journal.records
        journal.close()
        self.assertIsNone(journal._file)
        with Journal(self.path) as recovered:
            self.assertEqual(recovered.records, (b'uncertain',))

    def test_native_close_failure_retains_handle(self):
        journal = Journal(self.path, create=True)
        self.addCleanup(journal.close)
        file = journal._file
        handle = file.h
        with patch.object(file.k, 'CloseHandle', return_value=0):
            with self.assertRaises(OSError):
                journal.close()
            self.assertEqual(file.h, handle)
            self.assertIs(journal._file, file)
        journal.close()
        self.assertIsNone(file.h)

    def test_context_cleanup_does_not_mask_unknown(self):
        journal = Journal(self.path, create=True)
        self.addCleanup(journal.close)
        file = journal._file
        with patch.object(file, 'flush', side_effect=OSError('flush uncertainty')), \
                patch.object(file, 'close', side_effect=OSError('close uncertainty')):
            with self.assertRaisesRegex(JournalError, 'RESULT_UNKNOWN') as result:
                with journal:
                    journal.append(b'uncertain')
            self.assertIs(journal._file, file)
            self.assertTrue(result.exception.__notes__)
        journal.close()


if __name__ == '__main__':
    unittest.main()
