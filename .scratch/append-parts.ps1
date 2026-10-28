<#
Append parts to an existing 稿件 through the creator centre, the way the browser route has to do it.

The upload form creates 稿件s; adding parts to one that already exists is the edit page
(`?type=edit&bvid=`), which carries 「添加分P」 of its own. Verified on 运维自动化项目实战
2026-10-12: 14 parts -> 16, both new rows reading 上传完成, and the 稿件 stayed is_only_self=1.

    powershell -File .scratch\append-parts.ps1 -Bvid BV13XaA6jE8n -ListFile .scratch\ops-files.txt

The file list is UTF-8, one absolute path per line, in the order the parts should appear -- the
edit page appends in the order the input receives them and offers no reordering afterwards.
#>
param(
    [Parameter(Mandatory = $true)][string]$Bvid,
    [Parameter(Mandatory = $true)][string]$ListFile,
    [int]$WaitSeconds = 2400
)

$ErrorActionPreference = 'Continue'
$ab = Join-Path $PSScriptRoot 'ab.ps1'

$files = @(Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_ -and $_.Trim() })
if (-not $files.Count) { Write-Output "no files in $ListFile"; exit 1 }

$expectedBefore = -1
function Get-Progress {
    $raw = & powershell -ExecutionPolicy Bypass -File $ab -JsFile (Join-Path $PSScriptRoot 'ab-js\check-edit-progress.js') -WaitSeconds 60 2>&1 |
        Select-Object -First 1
    $json = $raw -replace '^"', '' -replace '"$', '' -replace '\\"', '"'
    try { return $json | ConvertFrom-Json } catch { return $null }
}

Write-Output "== $Bvid : appending $($files.Count) part(s) =="
& powershell -ExecutionPolicy Bypass -File $ab -Command "open https://member.bilibili.com/platform/upload/video/frame?type=edit&bvid=$Bvid" -WaitSeconds 180 | Out-Null
Start-Sleep -Seconds 16

$before = Get-Progress
if ($before) {
    $expectedBefore = [int]$before.parts
    Write-Output "   edit page holds $expectedBefore part(s)"
}

& powershell -ExecutionPolicy Bypass -File $ab -JsFile (Join-Path $PSScriptRoot 'ab-js\tag-add-part.js') -WaitSeconds 60 | Out-Null
$port = @(Get-Content "$env:TEMP\ab-bili-profile\DevToolsActivePort")[0]
& node (Join-Path $PSScriptRoot 'cdp-click-attach.mjs') $port '#ab-add-part' @files

$target = $expectedBefore + $files.Count
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$last = ''
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 30
    $now = Get-Progress
    if (-not $now) { continue }
    $last = "parts=$($now.parts) uploading=$($now.uploading)"
    Write-Output "   $last"
    if ([int]$now.parts -ge $target -and [int]$now.uploading -eq 0) { break }
}

if ($last -notmatch 'uploading=0') {
    Write-Output "!! still uploading after $WaitSeconds s: $last"
}

& powershell -ExecutionPolicy Bypass -File $ab -Command "eval window.scrollTo(0, document.body.scrollHeight)" -WaitSeconds 60 | Out-Null
Start-Sleep -Seconds 2
& powershell -ExecutionPolicy Bypass -File $ab -JsFile (Join-Path $PSScriptRoot 'ab-js\tag-submit.js') -WaitSeconds 60 | Out-Null
& powershell -ExecutionPolicy Bypass -File $ab -Command "click #ab-submit" -WaitSeconds 60 | Out-Null
Start-Sleep -Seconds 15

Write-Output "== submitted; verify with: biliup show $Bvid =="
