#requires -Version 7.0
param(
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $File $($Arguments -join ' ')" }
}

if (-not $PythonPath) {
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if (-not $candidate) { throw 'Python 3.12 was not found. Install Python 3.12 or pass -PythonPath with its executable path.' }
    $PythonPath = $candidate.Source
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
$version = & $PythonPath -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>$null
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '3.12') { throw "A real Python 3.12 executable is required; '$PythonPath' reported '$version'. Pass -PythonPath explicitly if needed." }

Push-Location $root
try {
    if (-not (Test-Path $venvPython)) { Invoke-Native $PythonPath @('-m', 'venv', '.venv') }
    $venvVersion = & $venvPython -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>$null
    if ($LASTEXITCODE -ne 0 -or $venvVersion.Trim() -ne '3.12') { throw "Existing .venv is not usable with Python 3.12: $venvPython" }
    Invoke-Native $venvPython @('-m', 'pip', 'install', '-r', 'requirements-dev.txt')
    Invoke-Native $venvPython @('-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', '.')
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22.18+ and pnpm 10.17.1 using your preferred method, then rerun setup. No global tools are installed by this script.' }
    Push-Location (Join-Path $root 'ui')
    try { Invoke-Native 'pnpm' @('install', '--frozen-lockfile') } finally { Pop-Location }
    Invoke-Native 'dotnet' @('restore', 'Aegis.slnx', '--locked-mode')
    Write-Output "Environment ready: $venvPython"
} finally { Pop-Location }
