[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Destination,
    [string]$GatewayDirectory,
    [string]$DesktopDirectory,
    [string]$UiDirectory,
    [string]$PythonEnvironment,
    [string]$DotnetDirectory
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$source = @{
    Gateway = if ($GatewayDirectory) { $GatewayDirectory } else { Join-Path $root 'src/Aegis.Gateway/bin/Release/net10.0' }
    Desktop = if ($DesktopDirectory) { $DesktopDirectory } else { Join-Path $root 'src/Aegis.Desktop/bin/Release/net10.0-windows' }
    Ui = if ($UiDirectory) { $UiDirectory } else { Join-Path $root 'ui/dist' }
}
$dest = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination))
$parent = Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($dest))
$targetExists = Get-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue
if (!$parent.PSIsContainer -or ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $targetExists) {
    throw 'Stage under an existing local directory and choose a new destination. Existing destinations are preserved.'
}

function Get-PlainFiles([string]$Directory) {
    $directoryItem = Get-Item -LiteralPath $Directory
    if (!$directoryItem.PSIsContainer -or ($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a local directory without a reparse point: $Directory"
    }
    foreach ($item in Get-ChildItem -LiteralPath $directoryItem.FullName -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Package source contains a link or junction: $($item.FullName)"
        }
        if ($item.PSIsContainer) { Get-PlainFiles $item.FullName } else { $item }
    }
}

function Assert-X64Executable([string]$Path) {
    $reader = [IO.BinaryReader]::new([IO.File]::OpenRead($Path))
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) { throw "Missing PE header: $Path" }
        $reader.BaseStream.Position = 0x3C
        $reader.BaseStream.Position = $reader.ReadUInt32()
        if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw "Expected an x64 Windows app host: $Path"
        }
    } finally { $reader.Dispose() }
}

function Write-PackageFile([string]$Path, [string]$RelativePath) {
    $file = Get-Item -LiteralPath $Path
    if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Package source must be a regular file: $Path"
    }
    $target = Join-Path $stage $RelativePath
    $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
    [IO.File]::Copy($file.FullName, $target, $false)
}

function Write-PackageTree([string]$Directory, [string]$RelativePath) {
    $from = (Get-Item -LiteralPath $Directory).FullName.TrimEnd('\')
    foreach ($file in Get-PlainFiles $from) {
        Write-PackageFile $file.FullName (Join-Path $RelativePath $file.FullName.Substring($from.Length + 1))
    }
}

function Write-AppOutput([string]$Directory, [string]$RelativePath, [string]$App) {
    $from = Get-Item -LiteralPath $Directory
    if (!$from.PSIsContainer -or ($from.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a plain build directory: $Directory"
    }
    Assert-X64Executable (Join-Path $from.FullName "$App.exe")
    foreach ($name in @("$App.exe", "$App.dll", "$App.deps.json", "$App.runtimeconfig.json")) {
        Write-PackageFile (Join-Path $from.FullName $name) (Join-Path $RelativePath $name)
    }
    foreach ($assembly in Get-ChildItem -LiteralPath $from.FullName -Filter '*.dll' -File) {
        if ($assembly.Name -ne "$App.dll") { Write-PackageFile $assembly.FullName (Join-Path $RelativePath $assembly.Name) }
    }
    foreach ($subdirectory in @('runtimes', 'Assets')) {
        $folder = Join-Path $from.FullName $subdirectory
        if (Test-Path -LiteralPath $folder) { Write-PackageTree $folder (Join-Path $RelativePath $subdirectory) }
    }
}

function Write-PythonLibrary([string]$Directory, [string]$RelativePath) {
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.Name -in @('__pycache__', 'site-packages', 'test', 'tests', 'ensurepip', 'idlelib', 'tkinter', 'turtledemo')) { continue }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Python runtime contains a link or junction: $($item.FullName)"
        }
        $relative = Join-Path $RelativePath $item.Name
        if ($item.PSIsContainer) { Write-PythonLibrary $item.FullName $relative }
        elseif ($item.Extension -ne '.pyc') { Write-PackageFile $item.FullName $relative }
    }
}

function Write-PrivateRuntime {
    $python = (Get-Item -LiteralPath $PythonEnvironment).FullName
    Assert-X64Executable $python
    $planFile = Join-Path $stage 'python-files.json'
    & $python -I -B (Join-Path $PSScriptRoot 'windows_release/runtime_files.py') (Join-Path $root 'pyproject.toml') $planFile
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $planFile -PathType Leaf)) {
        throw 'The staging environment must contain the product Python dependencies and the packaging build helper.'
    }
    $plan = Get-Content -LiteralPath $planFile -Raw -Encoding utf8 | ConvertFrom-Json
    Remove-Item -LiteralPath $planFile
    Assert-X64Executable (Join-Path $plan.base 'python.exe')
    foreach ($name in @('python.exe','pythonw.exe','python312.dll','python3.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt')) {
        Write-PackageFile (Join-Path $plan.base $name) (Join-Path 'runtime/python' $name)
    }
    Write-PackageTree (Join-Path $plan.base 'DLLs') 'runtime/python/DLLs'
    Write-PythonLibrary (Join-Path $plan.base 'Lib') 'runtime/python/Lib'
    foreach ($file in $plan.files) {
        Write-PackageFile (Join-Path $plan.site $file) (Join-Path 'runtime/python/Lib/site-packages' $file)
    }

    $dotnet = if ($DotnetDirectory) { (Get-Item -LiteralPath $DotnetDirectory).FullName }
        else { Split-Path (Get-Command dotnet.exe -CommandType Application | Select-Object -First 1 -ExpandProperty Source) }
    Assert-X64Executable (Join-Path $dotnet 'dotnet.exe')
    $frameworks = @('Microsoft.NETCore.App','Microsoft.AspNetCore.App','Microsoft.WindowsDesktop.App')
    $versions = @(Get-ChildItem -LiteralPath (Join-Path $dotnet 'host/fxr') -Directory |
        Where-Object Name -Match '^10\.0\.[0-9]+$' | Sort-Object { [version]$_.Name } -Descending)
    $runtimeVersion = $null
    foreach ($version in $versions) {
        $complete = $true
        foreach ($framework in $frameworks) {
            if (!(Test-Path -LiteralPath (Join-Path $dotnet "shared/$framework/$($version.Name)") -PathType Container)) {
                $complete = $false
            }
        }
        if ($complete) { $runtimeVersion = $version.Name; break }
    }
    if (!$runtimeVersion) { throw 'A complete .NET 10 x64 runtime set with a shared patch version is required.' }
    foreach ($name in @('dotnet.exe','LICENSE.txt','ThirdPartyNotices.txt')) {
        Write-PackageFile (Join-Path $dotnet $name) (Join-Path 'runtime/dotnet' $name)
    }
    Write-PackageTree (Join-Path $dotnet "host/fxr/$runtimeVersion") "runtime/dotnet/host/fxr/$runtimeVersion"
    foreach ($framework in $frameworks) {
        Write-PackageTree (Join-Path $dotnet "shared/$framework/$runtimeVersion") "runtime/dotnet/shared/$framework/$runtimeVersion"
    }
    $inventory = @{
        schemaVersion = 1
        platform = 'win-x64'
        pythonVersion = $plan.pythonVersion
        pythonDistributions = $plan.distributions
        dotnetVersion = $runtimeVersion
        webView2 = 'system-prerequisite'
    }
    [IO.File]::WriteAllText((Join-Path $stage 'runtime/runtime.json'), ($inventory | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}

$stage = Join-Path $parent.FullName ('.aegis-staging-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -Path $stage -ItemType Directory
try {
    Write-AppOutput $source.Gateway 'src/Aegis.Gateway/bin/Release/net10.0' 'Aegis.Gateway'
    Write-AppOutput $source.Desktop 'src/Aegis.Desktop/bin/Release/net10.0-windows' 'Aegis.Desktop'
    $ui = Get-Item -LiteralPath $source.Ui
    if (!$ui.PSIsContainer -or ($ui.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        !(Test-Path -LiteralPath (Join-Path $ui.FullName 'assets') -PathType Container)) {
        throw 'Build the React UI and select its dist folder before packaging.'
    }
    Write-PackageFile (Join-Path $ui.FullName 'index.html') 'ui/dist/index.html'
    foreach ($folder in @('assets', 'fonts')) {
        $built = Join-Path $ui.FullName $folder
        if (Test-Path -LiteralPath $built) { Write-PackageTree $built (Join-Path 'ui/dist' $folder) }
    }
    foreach ($folder in @('src/aegis', 'emulators/Aegis.Emulator/aegis_emulator')) {
        foreach ($module in Get-ChildItem -LiteralPath (Join-Path $root $folder) -Filter '*.py' -File) {
            Write-PackageFile $module.FullName (Join-Path $folder $module.Name)
        }
    }
    foreach ($file in @('scripts/run_hybrid_demo.py', 'scripts/benchmark_emulators.py', 'pyproject.toml', 'requirements-dev.txt', 'LEGAL_IP_CHECKLIST.md')) {
        Write-PackageFile (Join-Path $root $file) $file
    }
    Write-PackageFile (Join-Path $PSScriptRoot 'windows_release/launch.py') 'scripts/start_windows_release.py'
    Write-PackageFile (Join-Path $PSScriptRoot 'windows_release/Aegis.cmd') 'Aegis.cmd'
    Write-PackageFile (Join-Path $PSScriptRoot 'windows_release/Install-Aegis.cmd') 'Install-Aegis.cmd'
    Write-PackageFile (Join-Path $PSScriptRoot 'windows_release/manage_install.py') 'scripts/manage_install.py'
    Write-PackageFile (Join-Path $PSScriptRoot 'windows_release/README.ko.md') 'README.ko.md'

    if ($PythonEnvironment) { Write-PrivateRuntime }
    elseif ($DotnetDirectory) { throw 'Specify -PythonEnvironment when packaging a private runtime.' }

    $manifest = @{
        schemaVersion = 1
        platform = 'win-x64'
        files = @(Get-PlainFiles $stage | Sort-Object FullName | ForEach-Object {
            @{
                path = $_.FullName.Substring($stage.Length + 1).Replace('\', '/')
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        })
    }
    [IO.File]::WriteAllText((Join-Path $stage 'manifest.json'), ($manifest | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    $ready = Get-Item -LiteralPath $stage
    if (!$ready.PSIsContainer -or ($ready.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $ready.Parent.FullName -ne $parent.FullName) {
        throw 'The staged package must remain under the destination parent.'
    }
    [IO.Directory]::Move($stage, $dest)
    Write-Output $dest
} finally {
    $failed = Get-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
    if ($failed -and $failed.PSIsContainer -and
        !($failed.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
        $failed.Parent.FullName -eq $parent.FullName -and $failed.Name -match '^\.aegis-staging-[a-f0-9]{32}$') {
        Remove-Item -LiteralPath $failed.FullName -Recurse -Force
    }
}
