import ctypes
import json
import os
import subprocess
import sys

import pytest

from aegis.windows_fixture import _local_path


@pytest.mark.parametrize('path', [r'\\.\NUL', r'\\?\C:\fixture.json',
    r'\\server\share\fixture.json', 'fixture.json:stream', 'NUL', 'CON.txt',
    'COM1.json', 'LPT².txt', 'a/NUL.json', 'a. ', 'a?', 'a\x00b', b'file'])
def test_device_network_stream_and_ambiguous_paths_are_rejected(path):
    with pytest.raises((ValueError, TypeError)):
        _local_path(path)


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows handle tests')
class TestWindowsFixture:
    def test_exact_bytes_bounds_and_handle_release(self, tmp_path):
        from aegis.windows_fixture import read_fixture
        path = tmp_path / '한글 fixture.json'
        path.write_bytes(b'\x00\r\n\xff' * 40000)
        original = path.read_bytes()
        assert read_fixture(path, len(original)) == original
        assert read_fixture(path, 10) == original[:11]
        assert path.read_bytes() == original
        path.rename(tmp_path / 'renamed.json')

    def test_missing_and_directory_fail(self, tmp_path):
        from aegis.windows_fixture import read_fixture
        with pytest.raises(OSError):
            read_fixture(tmp_path / 'missing.json', 100)
        with pytest.raises((OSError, ValueError)):
            read_fixture(tmp_path, 100)

    def test_existing_writer_blocks_read_without_changing_bytes(self, tmp_path):
        from aegis.windows_fixture import read_fixture
        path = tmp_path / 'fixture.json'
        path.write_bytes(b'original')
        with path.open('r+b'):
            with pytest.raises(OSError) as failure:
                read_fixture(path, 100)
            assert failure.value.winerror == 32
        assert read_fixture(path, 100) == b'original'

    def test_final_symlink_is_not_followed(self, tmp_path):
        from aegis.windows_fixture import read_fixture
        target = tmp_path / 'target.json'
        target.write_bytes(b'secret')
        link = tmp_path / 'link.json'
        try:
            link.symlink_to(target)
        except OSError as error:
            if error.winerror == 1314:
                pytest.skip('Windows symlink privilege not available')
            raise
        with pytest.raises((ValueError, OSError)):
            read_fixture(link, 100)
        assert target.read_bytes() == b'secret'

    def test_adapter_cli_reads_fixture_without_persistence(self, tmp_path):
        from test_adapter_input import fixture, page
        source = tmp_path / 'fixture.json'
        source.write_text(json.dumps(fixture(page())), encoding='utf-8')
        before = source.read_bytes()
        result = subprocess.run([sys.executable, '-m', 'aegis.adapter_input',
            '--fixture', str(source)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PYTHONPATH=os.path.abspath('src')))
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report['status'] == 'END_OF_SYNTHETIC_INPUT'
        assert report['persistenceStatus'] == 'NOT_PERSISTED'
        assert source.read_bytes() == before
        assert list(tmp_path.iterdir()) == [source]

    def test_adapter_limit_is_still_enforced(self, tmp_path, monkeypatch):
        from aegis import adapter_input
        source = tmp_path / 'fixture.json'
        source.write_bytes(b'12345')
        monkeypatch.setattr(adapter_input, 'MAX_FIXTURE_BYTES', 4)
        with pytest.raises(adapter_input.AdapterError) as failure:
            adapter_input._load_fixture(source)
        assert failure.value.code == 'INPUT_LIMIT'

    @pytest.mark.parametrize('fault', ['reparse', 'metadata', 'read'])
    def test_handle_faults_close_handle_and_leave_source_unchanged(self, tmp_path, monkeypatch, fault):
        from aegis import windows_fixture
        path = tmp_path / 'fixture.json'
        path.write_bytes(b'original')
        dll = ctypes.WinDLL
        checks = []

        class Function:
            def __init__(self, real, name):
                object.__setattr__(self, 'real', real)
                object.__setattr__(self, 'name', name)

            def __setattr__(self, name, value):
                setattr(self.real, name, value)

            def __call__(self, *args):
                if self.name == 'CloseHandle':
                    checks.append('close')
                if self.name == 'GetFileInformationByHandleEx':
                    if fault == 'metadata':
                        ctypes.set_last_error(5)
                        return 0
                    result = self.real(*args)
                    if fault == 'reparse':
                        args[2]._obj.attributes |= 0x400
                    return result
                if self.name == 'ReadFile' and fault == 'read':
                    # The read handle also prevents a new writer/delete open.
                    with pytest.raises(PermissionError):
                        path.open('r+b')
                    with pytest.raises(PermissionError):
                        path.rename(tmp_path / 'moved.json')
                    ctypes.set_last_error(5)
                    return 0
                return self.real(*args)

        class Kernel:
            def __init__(self):
                self.real = dll('kernel32', use_last_error=True)
                self.functions = {}

            def __getattr__(self, name):
                if name not in self.functions:
                    self.functions[name] = Function(getattr(self.real, name), name)
                return self.functions[name]

        monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: Kernel())
        with pytest.raises((ValueError, OSError)):
            windows_fixture.read_fixture(path, 100)
        assert checks == ['close']
        assert path.read_bytes() == b'original'
        path.rename(tmp_path / 'after-close.json')
