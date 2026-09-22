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
#   powershell -ExecutionPolicy Bypass -File tools/covers/backgrounds.ps1 -Key a,b,c
#
# Existing images are skipped, so this is safe to re-run after adding a card.
#
# -Key arrives differently depending on how the script is started: `& script.ps1 -Key a,b`
# binds a real array, while `powershell -File script.ps1 -Key a,b` hands over the single
# string "a,b" because -File passes every argument verbatim. Splitting here means both
# spellings select the same cards; without it the -File form silently matched nothing and
# the script reported success having generated no art.

param([string[]]$Key)

$Key = @($Key | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() })

$ErrorActionPreference = 'Continue'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here '..\..')
$root = if ($env:COVERS_ROOT) { $env:COVERS_ROOT } else { Join-Path $repo 'build\covers' }
$bgDir = Join-Path $root 'bg'
$logDir = Join-Path $root 'logs'
$genRoot = Join-Path $env:USERPROFILE '.codex\generated_images'
New-Item -ItemType Directory -Force -Path $bgDir, $logDir | Out-Null

$cards = (Get-Content (Join-Path $here 'cards.json') -Raw -Encoding UTF8 | ConvertFrom-Json).cards

# Mirror a proxy into the environment for the child process.
#
# Two sources, because the system-proxy toggle and the VPN client are independent: the client
# can be running and reachable while `ProxyEnable` sits at 0, and reading only the registry
# then concludes "no proxy" and lets the CLI connect direct. On this machine that looked like
# a ten-minute hang rather than an error. So when the toggle is off, probe the ports a local
# client conventionally listens on and use the first one that accepts a connection.
$reg = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
$proxyUrl = $null
if ($reg.ProxyEnable -eq 1 -and $reg.ProxyServer) {
  $proxyUrl = if ($reg.ProxyServer -match '^https?://') { $reg.ProxyServer } else { "http://$($reg.ProxyServer)" }
  Write-Output "proxy   : $proxyUrl (from Windows system settings)"
} else {
  foreach ($port in 7897, 7890, 10809, 10808, 1080, 2080) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
      $pending = $client.BeginConnect('127.0.0.1', $port, $null, $null)
      if ($pending.AsyncWaitHandle.WaitOne(400) -and $client.Connected) {
        $proxyUrl = "http://127.0.0.1:$port"
        Write-Output "proxy   : $proxyUrl (probed; system proxy toggle is off)"
        break
      }
    } catch {
      # port closed; try the next one
    } finally {
      $client.Close()
    }
  }
  if (-not $proxyUrl) { Write-Output 'proxy   : none found; direct connection only' }
}
if ($proxyUrl -and -not $env:HTTPS_PROXY) {
  $env:HTTPS_PROXY = $proxyUrl; $env:HTTP_PROXY = $proxyUrl
  $env:https_proxy = $proxyUrl; $env:http_proxy = $proxyUrl
}

$style = @'
Generate one wide 16:9 abstract 3D technology illustration that will be used as the background art of a programming course cover.
STYLE: premium, clean, modern isometric 3D render; frosted glass and emissive light; soft bloom; faint grid floor; subtle depth of field; high-end SaaS hero art quality.
COMPOSITION: the main subject cluster sits in the RIGHT half of the frame; the LEFT half must stay dark, empty and uncluttered so that text can be overlaid there.
STRICT: absolutely no text, no letters, no numbers, no symbols, no logos, no watermarks and no UI screenshots anywhere in the image.
Output a single image.
'@

$targets = @($cards | Where-Object { -not $Key -or $Key -contains $_.key })
if (-not $targets) {
  $known = ($cards | ForEach-Object { $_.key }) -join ', '
  Write-Output "no card matched -Key '$($Key -join ',')'"
  Write-Output "known keys: $known"
  exit 1
}

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
