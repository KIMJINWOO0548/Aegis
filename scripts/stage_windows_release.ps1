[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Destination,
    [string]$GatewayDirectory,
    [string]$DesktopDirectory,
    [string]$UiDirectory
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
    Write-PackageFile (Join-Path $PSScriptRoot 'windows_release/README.ko.md') 'README.ko.md'

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
