import json
import os
import subprocess
import sys
from types import SimpleNamespace


def test_windows_reports_blocked_without_claiming_runtime_validation():
    from aegis.platform_support import report
    result = report(SimpleNamespace(name="nt"))
    assert result["status"] == "BLOCKED"
    assert result["runtimeValidation"] == "NOT_RUN"
    assert all(row["status"] == "PLATFORM_UNVERIFIED" for row in result["features"])
    assert result["features"][0]["missing"] == ["posix", "O_NOFOLLOW"]


def test_primitives_present_are_not_a_safety_or_test_pass():
    from aegis.platform_support import report
    result = report(SimpleNamespace(name="posix", O_NOFOLLOW=1, O_DIRECTORY=2))
    assert result["status"] == "PRIMITIVES_PRESENT"
    assert result["runtimeValidation"] == "NOT_RUN"
    assert all(not row["missing"] for row in result["features"])


def test_missing_directory_flag_blocks_only_features_that_need_it():
    from aegis.platform_support import report
    rows = {r["name"]: r for r in report(SimpleNamespace(name="posix", O_NOFOLLOW=1))["features"]}
    assert rows["adapter_fixture"]["status"] == "PRIMITIVES_PRESENT"
    assert rows["adapter_store"]["missing"] == ["O_DIRECTORY"]
    assert rows["archive_backup_restore"]["missing"] == ["O_DIRECTORY"]


def test_cli_is_json_and_writes_no_files(tmp_path):
    env = dict(os.environ, PYTHONPATH=os.path.abspath("src"))
    result = subprocess.run([sys.executable, "-m", "aegis.platform_support"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=10)
    body = json.loads(result.stdout)
    assert result.returncode == (2 if body["status"] == "BLOCKED" else 0)
    assert body["runtimeValidation"] == "NOT_RUN"
    assert not result.stderr
    assert list(tmp_path.iterdir()) == []
