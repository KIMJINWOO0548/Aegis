"""Exercise PowerShell's real argument parsing at the verification command boundary."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_verifier_keeps_junit_output_as_one_unicode_path_argument(tmp_path):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("The Windows delivery scripts require PowerShell 7.")
    root = tmp_path / "지원 검증 copy"
    script = Path(__file__).resolve().parents[1] / "scripts/verify.ps1"
    command = r"""
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:AEGIS_TEST_SCRIPT, [ref]$null, [ref]$null)
$root = $env:AEGIS_TEST_ROOT
$results = Join-Path $root 'TestResults'
$python = 'python.exe'
function Invoke-Check {
    param([string]$Name, [string]$File, [string[]]$Arguments, [string]$WorkingDirectory)
    ConvertTo-Json -InputObject @($Arguments) -Compress
}
$call = $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -eq 'Invoke-Check' -and $node.CommandElements[1].Value -eq 'python-pytest'}, $true)
if (@($call).Count -ne 1) { throw 'Expected one pytest verification command.' }
Invoke-Expression $call[0].Extent.Text
"""
    result = subprocess.run([powershell, "-NoProfile", "-Command", command],
        env=dict(os.environ, AEGIS_TEST_SCRIPT=str(script), AEGIS_TEST_ROOT=str(root)),
        encoding="utf-8", capture_output=True, timeout=20, check=True)
    arguments = json.loads(result.stdout)
    assert arguments == ["-m", "pytest", "--junitxml", str(root / "TestResults/python.xml")]
