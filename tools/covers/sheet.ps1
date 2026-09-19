# Builds one contact sheet per aspect ratio so the whole set can be judged at once.
#
# Posters are checked as a set or not at all: a card that reads fine alone can still be
# the one whose title sits at a different height, whose art fights the text, or whose
# palette is the odd one out. ffmpeg's image sequence input needs numbered files, so the
# cards are hard-linked into a numbered run first.
#
#   powershell -ExecutionPolicy Bypass -File tools/covers/sheet.ps1 [-Ratio 16x9|4x3]

param(
  [string]$Ratio = '16x9',
  [int]$Cols = 3,
  [int]$CellW = 470
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $here '..\..')
$root = if ($env:COVERS_ROOT) { $env:COVERS_ROOT } else { Join-Path $repo 'build\covers' }
$cards = (Get-Content (Join-Path $here 'cards.json') -Raw -Encoding UTF8 | ConvertFrom-Json).cards

$seq = Join-Path $root "qc\$Ratio"
if (Test-Path $seq) { Remove-Item $seq -Recurse -Force }
New-Item -ItemType Directory -Force -Path $seq | Out-Null

$n = 0
foreach ($card in $cards) {
  $src = Join-Path (Join-Path (Join-Path $root 'out') $Ratio) "$($card.key).png"
  if (-not (Test-Path $src)) { continue }
  $n++
  Copy-Item $src (Join-Path $seq ('{0:D2}.png' -f $n))
}

$rows = [math]::Ceiling($n / $Cols)
$sheet = Join-Path $root "qc\sheet-$Ratio.png"
& ffmpeg -v error -y -start_number 1 -i "$seq\%02d.png" `
  -vf "scale=${CellW}:-1,tile=${Cols}x${rows}:padding=10:color=0x0a0a0a" -frames:v 1 $sheet

Write-Output ("{0} ({1} cards, {2} KB)" -f $sheet, $n, [math]::Round((Get-Item $sheet).Length / 1KB))
