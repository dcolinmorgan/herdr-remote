#!/usr/bin/env pwsh
# Build Herdi for Windows. Counterpart of herdi-mac/build.sh.
#
#   .\build.ps1                  # self-contained single exe (no .NET needed to run)
#   .\build.ps1 -Framework       # small exe, requires the .NET 8 Desktop Runtime
#   .\build.ps1 -Compress        # smaller exe, double the memory -- see the table
#   .\build.ps1 -Zip             # produce BOTH release assets (see below)
#   .\build.ps1 -Arch win-arm64  # ARM64 device
#
# Measured on 0.7.3, private bytes with the flyout open:
#
#              exe     memory   target machine needs
#   default   166 MB    80 MB   nothing
#   -Framework 25 MB    80 MB   .NET 8 Desktop Runtime
#   -Compress  70 MB   160 MB   nothing
#
# -Compress saves 96 MB of download and costs 80 MB of memory for as long as the app is
# running: a compressed bundle cannot be mapped, so every assembly loaded is decompressed
# into private memory. Microsoft.Windows.SDK.NET.dll alone -- the Windows SDK's WinRT
# projection, which this app touches only for toasts -- is 25 MB of that.
#
# -Framework and the default cost exactly the same memory, because both map their
# assemblies off disk. The 6.6x size difference is the whole of what separates them, and
# it is paid on every update the built-in updater downloads.
#
# So -Zip publishes both, and the updater picks the one matching what is already installed
# (Updater.IsUsableAsset, against the deployment mode stamped in by Herdi.Win.csproj):
#
#   Herdi-<arch>-<version>.zip       self-contained, runs on a machine with no .NET
#   Herdi-<arch>-<version>-fdd.zip   framework-dependent, a sixth the download
#
# Both must be uploaded to the release. Publishing only the -fdd one would brick every
# self-contained install whose machine has no .NET 8 Desktop Runtime; publishing only the
# other just makes every update six times larger than it needs to be.
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
$version = '0.7.5'
$distDir = Join-Path $scriptDir 'dist'
$outDir = Join-Path $distDir $Arch

# NETSDK1176: the SDK only allows compression inside a self-contained bundle. Caught here
# rather than 30 seconds into a publish, which is where it used to surface.
if ($Compress -and $Framework) {
    throw '-Compress and -Framework are mutually exclusive: single-file compression is only supported for self-contained publishes (NETSDK1176).'
}

# -Zip builds both deployment modes, so a mode switch alongside it is a contradiction.
if ($Zip -and $Framework) {
    throw '-Zip already builds both deployment modes; drop -Framework.'
}

# Refused rather than warned about. A release asset decides what every install of it costs
# to run, and compression doubles that: 80 MB against 160 MB of private bytes, measured. A
# smaller download is not worth spending it on every user, every day, invisibly.
if ($Zip -and $Compress) {
    throw '-Zip and -Compress are mutually exclusive: release assets are published uncompressed because compression doubles the memory of every install that runs them.'
}

function Invoke-Publish {
    param(
        [Parameter(Mandatory)] [bool]$SelfContained,
        [Parameter(Mandatory)] [string]$OutDir,
        [Parameter(Mandatory)] [string]$Label
    )

    if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }

    # Not $args: that is an automatic variable, and assigning to it inside an advanced
    # script is asking for trouble.
    $publishArgs = @(
        'publish'
        '-c', 'Release'
        '-r', $Arch
        '-o', $OutDir
        '--nologo'
        '-p:PublishSingleFile=true'
        "-p:SelfContained=$($SelfContained.ToString().ToLower())"
        # The csproj leaves compression off because a compressed bundle cannot be
        # memory-mapped and has to be decompressed into private memory instead. Passed
        # explicitly because the SDK rejects it outright when SelfContained is false.
        "-p:EnableCompressionInSingleFile=$(($Compress -and $SelfContained).ToString().ToLower())"
    )

    Write-Host "  $Label"
    # Out-Host, not bare: this function returns the exe path, and a native command writes
    # its output to the success stream, so without this every line dotnet prints comes back
    # as part of the return value.
    & dotnet @publishArgs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed with exit code $LASTEXITCODE" }

    $exe = Join-Path $OutDir 'Herdi.exe'
    if (-not (Test-Path $exe)) { throw "Expected $exe to exist after publish." }
    return $exe
}

function New-Asset {
    param(
        [Parameter(Mandatory)] [string]$FromDir,
        [Parameter(Mandatory)] [string]$ZipPath
    )

    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Compress-Archive -Path (Join-Path $FromDir '*') -DestinationPath $ZipPath
    $mb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
    Write-Host "OK Packaged: $ZipPath ($mb MB)"
}

Write-Host '> Building release...'
$built = @()
Push-Location $scriptDir
try {
    if ($Zip) {
        # Both, because the updater picks per install and a release missing either one
        # strands the installs that needed it.
        $selfDir = Join-Path $distDir $Arch
        $fddDir = Join-Path $distDir "$Arch-fdd"
        $built += Invoke-Publish -SelfContained $true -OutDir $selfDir `
            -Label 'mode: self-contained single file'
        $built += Invoke-Publish -SelfContained $false -OutDir $fddDir `
            -Label 'mode: framework-dependent (requires .NET 8 Desktop Runtime)'
    }
    elseif ($Framework) {
        $built += Invoke-Publish -SelfContained $false -OutDir $outDir `
            -Label 'mode: framework-dependent (requires .NET 8 Desktop Runtime)'
    }
    else {
        $built += Invoke-Publish -SelfContained $true -OutDir $outDir `
            -Label "mode: self-contained single file$(if ($Compress) { ', compressed (smaller exe, double the memory)' })"
    }
}
finally {
    Pop-Location
}

foreach ($exe in $built) {
    $sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "OK Built: $exe ($sizeMb MB)"
}

if ($Zip) {
    # The updater matches these by RID and by the -fdd marker (Updater.IsUsableAsset).
    New-Asset -FromDir (Join-Path $distDir $Arch) -ZipPath (Join-Path $distDir "Herdi-$Arch-$version.zip")
    New-Asset -FromDir (Join-Path $distDir "$Arch-fdd") -ZipPath (Join-Path $distDir "Herdi-$Arch-$version-fdd.zip")
    Write-Host ''
    Write-Host 'Upload BOTH to the release. The updater picks per install:'
    Write-Host '  self-contained installs take the plain zip, framework-dependent ones the -fdd zip.'
}

Write-Host ''
Write-Host 'Run it:'
foreach ($exe in $built) { Write-Host "  $exe" }
Write-Host ''
Write-Host 'Notes:'
Write-Host '  - The exe is unsigned, so SmartScreen will warn on first launch.'
Write-Host '  - First run creates a Start Menu shortcut named Herdi. Do not delete it:'
Write-Host '    Windows resolves the toast identity (AppUserModelID) through it.'
Write-Host '  - Configure the relay URL from the tray menu: Settings...'
