#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$emulator = Join-Path $root 'emulators\Aegis.Emulator'
$results = Join-Path $root 'TestResults'
$evidence = Join-Path $root 'artifacts\checks'
New-Item -ItemType Directory -Force -Path $results, $evidence | Out-Null
$checks = [System.Collections.Generic.List[object]]::new()

function Invoke-Check {
    param([string]$Name, [string]$File, [string[]]$Arguments, [string]$WorkingDirectory = $root)
    $log = Join-Path $evidence ($Name + '.log')
    Push-Location $WorkingDirectory
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        & $File @Arguments 2>&1 | Tee-Object -FilePath $log
        $code = $LASTEXITCODE
        $clock.Stop()
        $status = if ($code -eq 0) { 'passed' } else { 'failed' }
        $checks.Add([pscustomobject]@{ name=$Name; status=$status; exitCode=$code; durationSeconds=[math]::Round($clock.Elapsed.TotalSeconds, 3); log=(Resolve-Path $log).Path })
    } catch {
        $clock.Stop()
        $_ | Out-String | Set-Content -LiteralPath $log
        $checks.Add([pscustomobject]@{ name=$Name; status='failed'; exitCode=-1; durationSeconds=[math]::Round($clock.Elapsed.TotalSeconds, 3); log=(Resolve-Path $log).Path })
    } finally { Pop-Location }
}

Invoke-Check 'contract-drift' $python @('scripts/generate_contracts.py', '--check')
Invoke-Check 'contract-v34' $python @('scripts/validate_v34.py')
Invoke-Check 'python-pytest' $python @('-m', 'pytest', '--junitxml', (Join-Path $results 'python.xml'))
Invoke-Check 'emulator-unittest' $python @('-m', 'unittest', 'discover', '-s', 'tests', '-v') $emulator
Invoke-Check 'ui-tests' 'pnpm' @('test') (Join-Path $root 'ui')
Invoke-Check 'ui-build' 'pnpm' @('build') (Join-Path $root 'ui')
Invoke-Check 'dotnet-restore' 'dotnet' @('restore', 'Aegis.slnx', '--locked-mode')
Invoke-Check 'dotnet-release-build' 'dotnet' @('build', 'Aegis.slnx', '--configuration', 'Release', '--no-restore')
Invoke-Check 'dotnet-tests' 'dotnet' @('test', 'Aegis.slnx', '--configuration', 'Release', '--no-build', '--logger', 'trx', '--results-directory', $results)
Invoke-Check 'gateway-inprocess-checks' 'dotnet' @('run', '--project', 'tests/Aegis.Gateway.Tests/Aegis.Gateway.Tests.csproj', '--configuration', 'Release', '--no-build', '--no-restore')
Invoke-Check 'selected-dotnet-inprocess-checks' 'dotnet' @('run', '--project', 'tests/Aegis.InProcessChecks/Aegis.InProcessChecks.csproj', '--configuration', 'Release', '--no-build', '--no-restore')

$summary = [pscustomobject]@{ generatedAtUtc=[DateTime]::UtcNow.ToString('o'); checks=$checks.ToArray(); overallStatus=$(if (@($checks | Where-Object status -ne 'passed').Count -eq 0) { 'passed' } else { 'failed' }) }
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding utf8
$summary | ConvertTo-Json -Depth 5
if ($summary.overallStatus -ne 'passed') { exit 1 }
