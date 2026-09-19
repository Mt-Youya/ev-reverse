# Copies finished posters next to the collections they belong to.
#
# The posters are rendered into a scratch tree; this is what puts them where a person
# browsing the exported courses will actually see them. It mirrors the platform's own
# catalog layout (`AI 大全栈/<category>/<collection>/`) rather than inventing one, and it
# leaves the videos alone.
#
#   powershell -ExecutionPolicy Bypass -File tools/covers/deliver.ps1 [-Root <export root>]
#
# This file is deliberately ASCII-only. Windows PowerShell 5.1 decodes a script without a
# BOM as ANSI, so the Chinese folder name and suffix live in cards.json -- opened as UTF-8 --
# instead of as literals here. As literals they were read as mojibake and the posters landed
# in a directory named `灏侀潰`, which nothing looks in.
#
# The file stem comes from the collection's own folder name rather than from the display
# title, so the name still matches the folder once the poster is separated from it. They get
# uploaded to object storage, where the folder is a key prefix and not something a human reads.

param([string]$Root)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here '..\..')
$scratch = if ($env:COVERS_ROOT) { $env:COVERS_ROOT } else { Join-Path $repo 'build\covers' }
$config = Get-Content (Join-Path $here 'cards.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$exportRoot = if ($Root) { $Root } else { Join-Path $repo $config.root }
$outDir = Join-Path $scratch 'out'

foreach ($card in $config.cards) {
  $stem = Split-Path $card.out -Leaf
  $dst = Join-Path (Join-Path $exportRoot $card.out) $config.coverDir
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  foreach ($ratio in @('16x9', '4x3')) {
    $src = Join-Path (Join-Path $outDir $ratio) "$($card.key).png"
    if (-not (Test-Path $src)) { Write-Output "MISSING $($card.key) [$ratio] -- run render.mjs first"; continue }
    $name = "$stem-$($config.coverSuffix)-$ratio.png"
    Copy-Item $src (Join-Path $dst $name) -Force
    Write-Output "$($card.key) [$ratio] -> $dst\$name"
  }
}
