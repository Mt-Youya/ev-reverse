<# Run on the host while the target video is playing in a Windows Sandbox session. #>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$toolName = Split-Path -Leaf $PSScriptRoot
$session = & wsb list | Select-String -Pattern '^[0-9a-fA-F-]{36}$' | Select-Object -First 1
if ($null -eq $session) { throw 'No active Windows Sandbox session was found.' }
$id = $session.Line.Trim()
& wsb share --id $id -f $root -s 'C:\EVTool' -w
if ($LASTEXITCODE -ne 0) { throw 'Could not share the tool folder with Windows Sandbox.' }
& wsb exec --id $id -r ExistingLogin -c "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\EVTool\$toolName\EVPlayer2_capture_keys.ps1 -InputPath C:\EVTool\EVPlayer2_download.zip -OutputDir C:\EVTool\$toolName"
if ($LASTEXITCODE -ne 0) {
    $errorFile = Join-Path $PSScriptRoot 'capture_error.txt'
    if (Test-Path -LiteralPath $errorFile) { Get-Content -LiteralPath $errorFile -Raw | Write-Error }
    throw 'Sandbox capture failed. Ensure EVPlayer2 is open, logged in, and playing the video that belongs to this ZIP.'
}
Write-Host "Created $(Join-Path $PSScriptRoot 'evplayer2_manifest.json')"
