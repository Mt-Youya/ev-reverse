<#
Front end for agent-browser. See docs/BROWSER-AUTOMATION.md for the traps this works around and
.scratch/ab-run.ps1 for why the work happens in a separate process.

    powershell -File .scratch\ab.ps1 -Command "get url"
    powershell -File .scratch\ab.ps1 -Spec  .scratch\ab-spec.json     # for paths with spaces/CJK
    powershell -File .scratch\ab.ps1 -Recover

-Command splits on whitespace, so it is for simple invocations only; anything carrying a file path
or a JavaScript expression goes through -Spec or -JsFile, where the argument is handed over whole
and cmd is never involved.
#>
param(
    [string]$Command,
    [string]$Spec,
    [string]$JsFile,
    [string]$Profile = "$env:TEMP\ab-bili-profile",
    [int]$WaitSeconds = 90,
    [switch]$Recover
)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$AB = 'C:\Users\Yonjay\.cargo\bin\agent-browser.exe'
$RUNNER = Join-Path $here 'ab-run.ps1'
$UTF8 = New-Object System.Text.UTF8Encoding($false)

function Read-Shared([string]$Path) {
    # The browser keeps the log open after the CLI that wrote it is gone, and a plain read fails
    # with "being used by another process" -- which is how a successful call used to look like a
    # crash. ReadAllText with an explicit share mode is the way past that.
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            $stream = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $text = $reader.ReadToEnd()
            $reader.Close()
            return $text
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    return ''
}

if ($Recover) {
    Get-Process -Name 'agent-browser' -ErrorAction SilentlyContinue | Stop-Process -Force
    # Only the browser launched against our own profile directory is touched, never the user's.
    $stale = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
        Where-Object { $_.CommandLine -like "*$Profile*" }
    foreach ($process in $stale) {
        Write-Output "killing stale chrome $($process.ProcessId) holding $Profile"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if (-not $stale) { Write-Output "no stale chrome holds $Profile" }
    return
}

if (-not $Spec) {
    $tag = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $Spec = Join-Path $env:TEMP "ab-spec-$tag.json"
    if ($JsFile) {
        if (-not (Test-Path -LiteralPath $JsFile)) { Write-Output "no such JS file: $JsFile"; return }
        $expression = [System.IO.File]::ReadAllText($JsFile, [System.Text.Encoding]::UTF8)
        $arguments = @('--profile', $Profile, 'eval', $expression.Trim())
    } elseif ($Command) {
        $arguments = @('--profile', $Profile) + ($Command -split '\s+' | Where-Object { $_ })
    } else {
        Write-Output 'give -Command, -Spec or -JsFile'
        return
    }
    $plan = [ordered]@{
        exe     = $AB
        args    = $arguments
        log     = (Join-Path $env:TEMP "ab-$tag.txt")
        err     = (Join-Path $env:TEMP "ab-$tag.txt.err")
        done    = (Join-Path $env:TEMP "ab-$tag.txt.done")
        timeout = $WaitSeconds
    }
    [System.IO.File]::WriteAllText($Spec, ($plan | ConvertTo-Json -Compress), $UTF8)
} else {
    $plan = [System.IO.File]::ReadAllText($Spec, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if (-not $plan.done) { Write-Output "spec $Spec has no 'done' path"; return }
}

function Invoke-Attempt {
    # A daemon that has just exited and one that is still starting look identical from here: the
    # CLI reports "Failed to read ... os error 10060" because it could not reach the socket yet.
    # Nothing was delivered to the page in that case, so retrying is safe even for click/upload --
    # which is the only reason this retry is allowed to exist.
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$RUNNER`" -Spec `"$Spec`""
    }
    if ($created.ReturnValue -ne 0) {
        return @{ text = "!! could not launch the runner (Win32_Process.Create returned $($created.ReturnValue))";
                  retry = $false }
    }

    $deadline = (Get-Date).AddSeconds($WaitSeconds + 30)
    while (-not (Test-Path -LiteralPath $plan.done) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
    }

    $pieces = @()
    if (Test-Path -LiteralPath $plan.log) { $pieces += (Read-Shared $plan.log) }
    if (Test-Path -LiteralPath $plan.err) { $pieces += (Read-Shared $plan.err) }
    $text = ($pieces -join "`n").TrimEnd()

    Remove-Item -LiteralPath $plan.done -Force -ErrorAction SilentlyContinue

    $retry = $text -match 'os error 10060' -or $text -match 'Failed to read:'
    if (-not (Test-Path -LiteralPath $plan.log) -and -not $text) {
        $text = "!! no output after $($WaitSeconds + 30)s"
    }
    return @{ text = $text; retry = $retry }
}

$attempt = Invoke-Attempt
$tries = 1
while ($attempt.retry -and $tries -lt 4) {
    $tries++
    Start-Sleep -Seconds 4
    $attempt = Invoke-Attempt
}

if ($attempt.text) { Write-Output $attempt.text }
if ($tries -gt 1 -and -not $attempt.retry) { Write-Output "-- delivered on attempt $tries --" }

if ($attempt.text -match 'Chrome exited before providing DevTools URL') {
    Write-Output '-- the profile is locked by a leftover browser; rerun with -Recover --'
}
