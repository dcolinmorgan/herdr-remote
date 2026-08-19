#!/usr/bin/env pwsh
# Build Herdi for Windows. Counterpart of herdi-mac/build.sh.
#
#   .\build.ps1                  # self-contained single exe (no .NET needed to run)
#   .\build.ps1 -Framework       # small exe, requires the .NET 8 Desktop Runtime
#   .\build.ps1 -Compress        # smaller exe, much larger resting memory (see below)
#   .\build.ps1 -Zip             # also produce the release asset the updater looks for
#   .\build.ps1 -Arch win-arm64  # ARM64 device
#
# ASCII only, deliberately. Windows PowerShell 5.1 reads .ps1 files as ANSI unless they
# carry a UTF-8 BOM, so on a non-Latin system locale a stray multi-byte character here is
# re-decoded as something else -- and one of them used to swallow a closing quote, which
# turned the notes at the bottom into code and failed the whole script after a successful
# build. Keeping this file to ASCII means the encoding can never matter.

[CmdletBinding()]
param(
    [string]$Arch = 'win-x64',
    [switch]$Framework,
    [switch]$Compress,
    [switch]$Zip
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = '0.7.3'
$distDir = Join-Path $scriptDir 'dist'
$outDir = Join-Path $distDir $Arch

# NETSDK1176: the SDK only allows compression inside a self-contained bundle. Caught here
# rather than 30 seconds into a publish, which is where it used to surface.
if ($Compress -and $Framework) {
    throw '-Compress and -Framework are mutually exclusive: single-file compression is only supported for self-contained publishes (NETSDK1176).'
}

Write-Host '> Building release...'
Push-Location $scriptDir
try {
    if (Test-Path $outDir) { Remove-Item $outDir -Recurse -Force }

    # Not $args: that is an automatic variable, and assigning to it inside an advanced
    # script is asking for trouble.
    $publishArgs = @(
        'publish'
        '-c', 'Release'
        '-r', $Arch
        '-o', $outDir
        '--nologo'
    )

    if ($Framework) {
        # Framework-dependent: a couple of MB, but the target machine needs
        # the .NET 8 Desktop Runtime installed.
        $publishArgs += @('-p:SelfContained=false', '-p:PublishSingleFile=true')
        Write-Host '  mode: framework-dependent (requires .NET 8 Desktop Runtime)'
    }
    else {
        # Self-contained single file - the closest match to dragging Herdi.app across.
        $publishArgs += @('-p:SelfContained=true', '-p:PublishSingleFile=true')
        Write-Host '  mode: self-contained single file'
    }

    # The csproj leaves compression off because a compressed bundle cannot be memory-mapped
    # and has to be decompressed into private memory instead. -Compress buys the download
    # size back at that cost; see the comment on EnableCompressionInSingleFile.
    $publishArgs += "-p:EnableCompressionInSingleFile=$($Compress.IsPresent.ToString().ToLower())"
    Write-Host "  compression: $(if ($Compress) { 'on (smaller exe, larger resting memory)' } else { 'off (mapped from the bundle)' })"

    & dotnet @publishArgs
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$exe = Join-Path $outDir 'Herdi.exe'
if (-not (Test-Path $exe)) { throw "Expected $exe to exist after publish." }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "OK Built: $exe ($sizeMb MB)"

if ($Zip) {
    # The updater matches release assets by *win*.zip (see Updater.HandleRelease).
    $zipPath = Join-Path $distDir "Herdi-$Arch-$version.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zipPath
    Write-Host "OK Packaged: $zipPath"
}

Write-Host ''
Write-Host 'Run it:'
Write-Host "  $exe"
Write-Host ''
Write-Host 'Notes:'
Write-Host '  - The exe is unsigned, so SmartScreen will warn on first launch.'
Write-Host '  - First run creates a Start Menu shortcut named Herdi. Do not delete it:'
Write-Host '    Windows resolves the toast identity (AppUserModelID) through it.'
Write-Host '  - Configure the relay URL from the tray menu: Settings...'
