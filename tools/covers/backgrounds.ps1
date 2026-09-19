# Generates one AI background image per collection poster.
#
# Why this goes through the Codex CLI rather than an images API: this machine has no
# OPENAI_API_KEY, and the only image model reachable from here is the one built into the
# Codex CLI that is already signed in. The CLI writes to
# $CODEX_HOME/generated_images/<session id>/, and it prints the session id, so a run can
# be attributed to its own output even when several run at once -- an earlier version
# diffed the directory and would hand one worker another worker's picture.
#
# The shell sandbox inside Codex is broken on this machine, so the agent cannot copy its
# own output out; this script does the copying instead.
#
# The Codex CLI is a Rust binary: it honours HTTP(S)_PROXY but ignores the Windows system
# proxy setting in the registry. Where the only route out is a local VPN client, it
# therefore connects direct and dies on a poisoned DNS answer -- which is exactly what
# happened here, and why the proxy is mirrored into the environment below.
#
#   powershell -ExecutionPolicy Bypass -File tools/covers/backgrounds.ps1 [-Key <key> ...]
#
# Existing images are skipped, so this is safe to re-run after adding a card.

param([string[]]$Key)

$ErrorActionPreference = 'Continue'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here '..\..')
$root = if ($env:COVERS_ROOT) { $env:COVERS_ROOT } else { Join-Path $repo 'build\covers' }
$bgDir = Join-Path $root 'bg'
$logDir = Join-Path $root 'logs'
$genRoot = Join-Path $env:USERPROFILE '.codex\generated_images'
New-Item -ItemType Directory -Force -Path $bgDir, $logDir | Out-Null

$cards = (Get-Content (Join-Path $here 'cards.json') -Raw -Encoding UTF8 | ConvertFrom-Json).cards

# Mirror the Windows system proxy into the environment for the child process.
$reg = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
if ($reg.ProxyEnable -eq 1 -and $reg.ProxyServer) {
  $proxyUrl = if ($reg.ProxyServer -match '^https?://') { $reg.ProxyServer } else { "http://$($reg.ProxyServer)" }
  if (-not $env:HTTPS_PROXY) {
    $env:HTTPS_PROXY = $proxyUrl; $env:HTTP_PROXY = $proxyUrl
    $env:https_proxy = $proxyUrl; $env:http_proxy = $proxyUrl
    Write-Output "proxy   : $proxyUrl (from Windows system settings)"
  }
} else {
  Write-Output 'proxy   : none configured in Windows settings; direct connection only'
}

$style = @'
Generate one wide 16:9 abstract 3D technology illustration that will be used as the background art of a programming course cover.
STYLE: premium, clean, modern isometric 3D render; frosted glass and emissive light; soft bloom; faint grid floor; subtle depth of field; high-end SaaS hero art quality.
COMPOSITION: the main subject cluster sits in the RIGHT half of the frame; the LEFT half must stay dark, empty and uncluttered so that text can be overlaid there.
STRICT: absolutely no text, no letters, no numbers, no symbols, no logos, no watermarks and no UI screenshots anywhere in the image.
Output a single image.
'@

$targets = $cards | Where-Object { -not $Key -or $Key -contains $_.key }

foreach ($card in $targets) {
  $target = Join-Path $bgDir "$($card.key).png"
  if (Test-Path $target) { Write-Output "SKIP $($card.key) (exists)"; continue }

  $ok = $false
  for ($attempt = 1; $attempt -le 3 -and -not $ok; $attempt++) {
    $before = @{}
    Get-ChildItem $genRoot -Recurse -File -ErrorAction SilentlyContinue |
      ForEach-Object { $before[$_.FullName] = $true }
    $started = Get-Date

    $prompt = "$style`nSCENE: $($card.bg.scene).`nPALETTE: $($card.bg.palette), dark background."
    $log = Join-Path $logDir "codex-$($card.key)-$attempt.log"
    $prompt | & codex exec --skip-git-repo-check -C $root -m gpt-5.5 `
      -c model_reasoning_effort='"low"' - *> $log

    $img = $null
    $m = Select-String -Path $log -Pattern 'session id:\s*([0-9a-fA-F-]{36})' -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($m) {
      $sid = $m.Matches[0].Groups[1].Value
      $img = Get-ChildItem (Join-Path $genRoot $sid) -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }
    if (-not $img) {
      # No session id in the log: fall back to whatever appeared during this attempt.
      $img = Get-ChildItem $genRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { -not $before.ContainsKey($_.FullName) -and $_.LastWriteTime -gt $started } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    }

    if ($img) {
      Copy-Item $img.FullName $target -Force
      Write-Output "OK   $($card.key) <- $($img.Name)"
      $ok = $true
    } else {
      Write-Output "MISS $($card.key) attempt $attempt (see $log)"
    }
  }
  if (-not $ok) { Write-Output "FAIL $($card.key)" }
}
Write-Output 'backgrounds done'
