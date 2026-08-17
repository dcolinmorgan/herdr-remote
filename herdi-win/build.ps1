#!/usr/bin/env pwsh
# Build Herdi for Windows. Counterpart of herdi-mac/build.sh.
#
#   .\build.ps1                  # self-contained single exe (no .NET needed to run)
#   .\build.ps1 -Framework       # small exe, requires the .NET 8 Desktop Runtime
#   .\build.ps1 -Zip             # also produce the release asset the updater looks for
#   .\build.ps1 -Arch win-arm64  # ARM64 device

[CmdletBinding()]
param(
    [string]$Arch = 'win-x64',
    [switch]$Framework,
    [switch]$Zip
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$version = '0.7.3'
$distDir = Join-Path $scriptDir 'dist'
$outDir = Join-Path $distDir $Arch

Write-Host '▸ Building release...'
Push-Location $scriptDir
try {
    if (Test-Path $outDir) { Remove-Item $outDir -Recurse -Force }

    $args = @(
        'publish'
        '-c', 'Release'
        '-r', $Arch
        '-o', $outDir
        '--nologo'
    )

    if ($Framework) {
        # Framework-dependent: a couple of MB, but the target machine needs
        # the .NET 8 Desktop Runtime installed.
        $args += @('-p:SelfContained=false', '-p:PublishSingleFile=true')
        Write-Host '  mode: framework-dependent (requires .NET 8 Desktop Runtime)'
    }
    else {
        # Self-contained single file — the closest match to dragging Herdi.app across.
        $args += @('-p:SelfContained=true', '-p:PublishSingleFile=true')
        Write-Host '  mode: self-contained single file'
    }

    & dotnet @args
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$exe = Join-Path $outDir 'Herdi.exe'
if (-not (Test-Path $exe)) { throw "Expected $exe to exist after publish." }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "✓ Built: $exe ($sizeMb MB)"

if ($Zip) {
    # The updater matches release assets by *win*.zip (see Updater.HandleRelease).
    $zipPath = Join-Path $distDir "Herdi-$Arch-$version.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zipPath
    Write-Host "✓ Packaged: $zipPath"
}

Write-Host ''
Write-Host 'Run it:'
Write-Host "  $exe"
Write-Host ''
Write-Host 'Notes:'
Write-Host '  · The exe is unsigned, so SmartScreen will warn on first launch.'
Write-Host '  · First run creates a Start Menu shortcut named Herdi. Do not delete it —'
Write-Host '    Windows resolves the toast identity (AppUserModelID) through it.'
Write-Host '  · Configure the relay URL from the tray menu: Relay Settings…'
