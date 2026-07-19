#Requires -Version 5.1

$ErrorActionPreference = "Stop"

function Import-RelayConfig {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmedLine = $line.Trim()
        if ($trimmedLine.Length -eq 0 -or $trimmedLine.StartsWith("#")) {
            continue
        }

        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }

        $name = $line.Substring(0, $separator).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            continue
        }

        $value = $line.Substring($separator + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last = $value[$value.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        $environmentPath = "Env:{0}" -f $name
        if (-not (Test-Path -LiteralPath $environmentPath)) {
            Set-Item -LiteralPath $environmentPath -Value $value
        }
    }
}

function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][Parameter(Mandatory = $true)][string]$Argument)

    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0

    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }

        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }

        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }

    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Start-ConsoleChild {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $argumentLine = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument -Argument $_ }) -join ' ')
    return Start-Process -FilePath $FilePath -ArgumentList $argumentLine -NoNewWindow -PassThru
}

function Stop-ChildProcessTree {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) {
        return
    }

    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
            & $taskkill /PID $Process.Id /T /F 2>$null | Out-Null
        }
    }
    catch {
        try {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        catch {
            # The process may already have exited.
        }
    }
    finally {
        $Process.Dispose()
    }
}

$homeDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$configFile = Join-Path $homeDirectory ".config\herdr-remote\config.env"
$secretsFile = Join-Path $homeDirectory ".config\herdr-remote\secrets.env"
$relayScript = Join-Path $PSScriptRoot "herdr_relay.py"

Import-RelayConfig -Path $configFile
Import-RelayConfig -Path $secretsFile

$port = $env:HERDR_RELAY_PORT
if ([string]::IsNullOrWhiteSpace($port)) {
    $port = "8375"
}

$relayHost = $env:HERDR_RELAY_HOST
if ([string]::IsNullOrWhiteSpace($relayHost)) {
    $env:HERDR_RELAY_HOST = "127.0.0.1"
}
else {
    $env:HERDR_RELAY_HOST = $relayHost.Trim()
}

$tunnelMode = $env:HERDR_TUNNEL_MODE
if ([string]::IsNullOrWhiteSpace($tunnelMode)) {
    $tunnelMode = "none"
}
else {
    $tunnelMode = $tunnelMode.Trim().ToLowerInvariant()
}

$tunnelName = $env:HERDR_TUNNEL_NAME
if ($null -ne $tunnelName) {
    $tunnelName = $tunnelName.Trim()
}

if ($tunnelMode -notin @("temp", "named", "none")) {
    Write-Error "Unsupported HERDR_TUNNEL_MODE '$tunnelMode'. Expected temp, named, or none." -ErrorAction Continue
    exit 1
}

$normalizedRelayHost = $env:HERDR_RELAY_HOST.Trim().Trim("[", "]").ToLowerInvariant()
$loopbackHosts = @("127.0.0.1", "::1", "localhost")
$requiresRelayAuth = $normalizedRelayHost -notin $loopbackHosts -or $tunnelMode -ne "none"
if ($requiresRelayAuth -and [string]::IsNullOrWhiteSpace($env:HERDR_RELAY_TOKEN)) {
    Write-Error "HERDR_RELAY_TOKEN is required for LAN binding or tunnel access." -ErrorAction Continue
    exit 1
}

$uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    Write-Error "uv is required to start the relay. Install it with: winget install --id astral-sh.uv -e" -ErrorAction Continue
    exit 1
}

if (-not (Test-Path -LiteralPath $relayScript -PathType Leaf)) {
    Write-Error "Relay script not found at $relayScript" -ErrorAction Continue
    exit 1
}

$relayProcess = $null
$tunnelProcess = $null
$exitCode = 0

Write-Host "herdr-remote relay"
Write-Host ""

try {
    Write-Host "Starting relay on :$port..."
    $relayProcess = Start-ConsoleChild -FilePath $uv.Source -Arguments @("run", $relayScript)
    Start-Sleep -Seconds 2
    $relayProcess.Refresh()

    if ($relayProcess.HasExited) {
        throw "Relay failed to start (exit code $($relayProcess.ExitCode)). Check whether port $port is already in use."
    }
    Write-Host "Relay running (pid $($relayProcess.Id))"

    $cloudflared = Get-Command cloudflared -CommandType Application -ErrorAction SilentlyContinue
    $effectiveTunnelMode = $tunnelMode
    $cloudflaredConfig = Join-Path $homeDirectory ".cloudflared\config-herdr.yml"

    if ($effectiveTunnelMode -eq "named") {
        if ([string]::IsNullOrWhiteSpace($tunnelName)) {
            Write-Warning "HERDR_TUNNEL_MODE=named requires HERDR_TUNNEL_NAME. Falling back to a temporary tunnel."
            $effectiveTunnelMode = "temp"
        }
        elseif (-not (Test-Path -LiteralPath $cloudflaredConfig -PathType Leaf)) {
            Write-Warning "Named tunnel config not found at $cloudflaredConfig. Falling back to a temporary tunnel."
            $effectiveTunnelMode = "temp"
        }
    }

    if ($effectiveTunnelMode -eq "none") {
        Write-Host "Tunnel disabled (config: HERDR_TUNNEL_MODE=none)"
    }
    elseif ($null -eq $cloudflared) {
        Write-Warning "cloudflared was not found; the relay is running locally only."
        Write-Host "Install it with: winget install --id Cloudflare.cloudflared -e"
    }
    else {
        try {
            if ($effectiveTunnelMode -eq "named") {
                Write-Host "Starting named tunnel ($tunnelName)..."
                $tunnelProcess = Start-ConsoleChild -FilePath $cloudflared.Source -Arguments @(
                    "tunnel", "--config", $cloudflaredConfig, "run", $tunnelName
                )
            }
            else {
                Write-Host "Starting temp tunnel..."
                $tunnelProcess = Start-ConsoleChild -FilePath $cloudflared.Source -Arguments @(
                    "tunnel", "--url", "http://localhost:$port"
                )
            }

            Start-Sleep -Seconds 4
            $tunnelProcess.Refresh()
            if ($tunnelProcess.HasExited) {
                Write-Warning "Tunnel failed to start (exit code $($tunnelProcess.ExitCode)); the relay is still running locally."
                $tunnelProcess.Dispose()
                $tunnelProcess = $null
            }
            elseif ($effectiveTunnelMode -eq "temp") {
                Write-Host "Tunnel started; the public URL appears in the cloudflared output above."
            }
            else {
                Write-Host "Named tunnel running (pid $($tunnelProcess.Id))"
            }
        }
        catch [System.Management.Automation.PipelineStoppedException] {
            throw
        }
        catch {
            if ($null -ne $tunnelProcess) {
                Stop-ChildProcessTree -Process $tunnelProcess
                $tunnelProcess = $null
            }
            Write-Warning "Tunnel failed to start ($($_.Exception.Message)); the relay is still running locally."
        }
    }

    Write-Host ""
    Write-Host "Ready. Press Ctrl+C to stop."
    Write-Host ""

    while (-not $relayProcess.HasExited) {
        Start-Sleep -Milliseconds 500
        $relayProcess.Refresh()
    }

    $exitCode = $relayProcess.ExitCode
    if ($exitCode -ne 0) {
        Write-Error "Relay exited with code $exitCode." -ErrorAction Continue
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    throw
}
catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    $exitCode = 1
}
finally {
    Write-Host ""
    Write-Host "Shutting down..."
    Stop-ChildProcessTree -Process $tunnelProcess
    Stop-ChildProcessTree -Process $relayProcess
    Write-Host "Done."
}

exit $exitCode
