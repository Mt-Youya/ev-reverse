// Lays out a collection poster in one of two aspect ratios and screenshots it with
// a headless browser.
//
// Why a browser instead of an image library: the words on these posters are Chinese,
// and every drawing library on this machine would need a CJK font loaded by hand and
// a text shaper to break lines sensibly. Chrome already has the fonts, already breaks
// lines, and already renders the CSS this layout is written in, so the poster is an
// HTML page that happens to be photographed. That also means the title cannot come out
// misspelled the way a generated image's lettering can -- the art behind it comes from
// `backgrounds.ps1` and never contains text at all.
//
// Two ratios get two layouts, not one layout scaled: 4:3 has the same height as 16:9 but
// a narrower measure, so the text column is parameterised and the title shrinks to fit.
//
//   node render.mjs [key ...]        # all cards when no key is named
//
// Cards come from cards.json. Art comes from <root>/bg/<key>.png when it exists; when it
// does not, a vector graph is drawn instead so the layout can still be checked offline.

import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const ROOT = process.env.COVERS_ROOT || path.join(REPO, 'build', 'covers');
const BG_DIR = path.join(ROOT, 'bg');
const OUT_DIR = path.join(ROOT, 'out');
const HTML_DIR = path.join(ROOT, 'html');

const cards = JSON.parse(fs.readFileSync(path.join(HERE, 'cards.json'), 'utf8')).cards;

function findBrowser() {
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ];
  const found = candidates.find((p) => fs.existsSync(p));
  if (!found) throw new Error('no Chrome or Edge found; set one of the paths in findBrowser()');
  return found;
}

const BROWSER = findBrowser();

// Same height in both ratios keeps the vertical rhythm identical; only the horizontal
// measure changes, and that is what the text column is parameterised on.
const RATIOS = [
  { key: '16x9', w: 1920, h: 1080, pad: 120, contentW: 1160, titleMax: 1140,
    tick: 56, ghostRight: 70, ghostSize: 430, ghostBottom: -132, titleSize: 128,
    bgPos: '72% center',
    scrim: `linear-gradient(100deg,
      rgba(4,6,12,1) 0%,
      rgba(4,6,12,.985) 25%,
      rgba(4,6,12,.90) 40%,
      rgba(4,6,12,.46) 56%,
      rgba(4,6,12,.08) 72%,
      rgba(4,6,12,.32) 100%)` },
  { key: '4x3', w: 1440, h: 1080, pad: 92, contentW: 880, titleMax: 880,
    tick: 44, ghostRight: 54, ghostSize: 330, ghostBottom: -104, titleSize: 124,
    bgPos: '78% center',
    scrim: `linear-gradient(100deg,
      rgba(4,6,12,1) 0%,
      rgba(4,6,12,.985) 30%,
      rgba(4,6,12,.92) 47%,
      rgba(4,6,12,.52) 63%,
      rgba(4,6,12,.12) 79%,
      rgba(4,6,12,.34) 100%)` },
];

const fileUrl = (p) => 'file:///' + p.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/');
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Keep runs of Chinese characters unbreakable so a two-word title never splits mid-term
// (a real failure: "Agents 底" / "层逻辑"). Balanced wrapping then breaks at the space,
// and the auto-fit pass below shrinks the type if an unbreakable run is still too wide.
const titleHtml = (s) => esc(s).replace(/[\u4e00-\u9fff]+/g, (m) => `<span class="nw">${m}</span>`);

// Fallback art: an abstract directed graph of glowing nodes and edges. Each card lights
// a different path so a set of fallbacks is not the same picture repeated; numeric cards
// walk the table in order and named cards hash their name onto it.
const HOT_PATHS = [
  [0, 1, 3, 4, 6], [3, 4, 6], [0, 2, 3], [0, 1, 3], [2, 3, 5, 6],
  [0, 2, 3, 5], [4, 6], [0, 1, 2, 3], [5, 6], [1, 3, 4], [0, 1, 3, 5],
];

function motifSvg(key, R) {
  const name = String(key);
  const idx = /^\d+$/.test(name)
    ? (Number(name) - 1 + HOT_PATHS.length) % HOT_PATHS.length
    : [...name].reduce((a, c) => a + c.charCodeAt(0), 0) % HOT_PATHS.length;
  const hot = new Set(HOT_PATHS[idx]);
  const N = [
    { x: 1150, y: 700, s: 96 }, { x: 1330, y: 520, s: 74 }, { x: 1345, y: 830, s: 66 },
    { x: 1550, y: 660, s: 88 }, { x: 1660, y: 430, s: 62 }, { x: 1720, y: 860, s: 58 },
    { x: 1845, y: 700, s: 54 },
  ];
  const E = [
    [0, 1, 1240, 590], [0, 2, 1246, 792], [1, 3, 1450, 566], [2, 3, 1452, 786],
    [3, 4, 1618, 536], [3, 5, 1652, 786], [4, 6, 1790, 540], [5, 6, 1808, 800],
  ];
  // Stop each edge just outside the target node so its arrowhead stays visible.
  const edges = E.map(([a, b, cx, cy]) => {
    const A = N[a], B = N[b];
    const dx = B.x - cx, dy = B.y - cy;
    const len = Math.hypot(dx, dy) || 1;
    const gap = B.s / 2 + 14;
    const ex = B.x - (dx / len) * gap, ey = B.y - (dy / len) * gap;
    const lit = hot.has(a) && hot.has(b) ? ' class="lit"' : '';
    return `<path${lit} d="M${A.x},${A.y} Q${cx},${cy} ${ex.toFixed(1)},${ey.toFixed(1)}"/>`;
  }).join('\n    ');
  const nodes = N.map((n, i) => {
    const x = n.x - n.s / 2, y = n.y - n.s / 2, r = Math.round(n.s * 0.20);
    const bar = `M${n.x - n.s * 0.27},${n.y} h${n.s * 0.54}`;
    const on = hot.has(i);
    return `<g class="node${on ? ' on' : ''}"${on ? ' filter="url(#glow)"' : ''}>
      <rect x="${x}" y="${y}" width="${n.s}" height="${n.s}" rx="${r}"/>
      <path class="bar" d="${bar}"/>
    </g>`;
  }).join('\n    ');

  // The 4:3 frame is narrower, so pull the graph left to keep it fully visible.
  const shift = R.key === '4x3' ? -330 : 0;

  return `<svg class="motif" viewBox="0 0 ${R.w} ${R.h}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <filter id="glow" x="-80%" y="-80%" width="260%" height="260%">
        <feGaussianBlur stdDeviation="7" result="b"/>
        <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
      <radialGradient id="halo" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="#22D3EE" stop-opacity=".18"/>
        <stop offset="100%" stop-color="#22D3EE" stop-opacity="0"/>
      </radialGradient>
      <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6"
        markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="rgba(125,205,255,.55)"/>
      </marker>
    </defs>
    <g transform="translate(${shift},0)">
      <ellipse cx="1500" cy="650" rx="580" ry="480" fill="url(#halo)"/>
      <g class="edges">
    ${edges}
      </g>
      <g class="nodes">
    ${nodes}
      </g>
      <g class="satellites">
        <circle cx="1256" cy="404" r="7"/><circle cx="1470" cy="300" r="5"/>
        <circle cx="1790" cy="252" r="6"/><circle cx="1064" cy="880" r="6"/>
        <circle cx="1666" cy="960" r="5"/><circle cx="1898" cy="500" r="5"/>
      </g>
    </g>
  </svg>`;
}

function cardHtml(card, R) {
  const bgFile = path.join(BG_DIR, `${card.key}.png`);
  const hasBg = fs.existsSync(bgFile);
  const bgCss = hasBg ? `background-image:url("${fileUrl(bgFile)}")` : '';
  const tags = card.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join('');

  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
  :root{ --w:${R.w}px; --h:${R.h}px; --pad:${R.pad}px; --accent:#22D3EE; --ink:#FFFFFF; }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:var(--w);height:var(--h);overflow:hidden;background:#04060C}
  body{font-family:"Microsoft YaHei","Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
  .poster{position:relative;width:var(--w);height:var(--h);overflow:hidden;background:
    radial-gradient(1200px 800px at 78% 42%, #16233F 0%, #0A1120 45%, #04060C 100%);}

  /* ---- layers ---- */
  .bg{position:absolute;inset:0;background-size:cover;background-position:${R.bgPos};${bgCss}}
  .scrim{position:absolute;inset:0;background:${R.scrim};}
  .grid{position:absolute;inset:0;opacity:.34;
    background-image:
      linear-gradient(rgba(148,180,220,.055) 1px, transparent 1px),
      linear-gradient(90deg, rgba(148,180,220,.055) 1px, transparent 1px);
    background-size:64px 64px, 64px 64px;
    -webkit-mask-image:radial-gradient(1000px 820px at 16% 50%, #000 0%, transparent 86%);
            mask-image:radial-gradient(1000px 820px at 16% 50%, #000 0%, transparent 86%);}
  .footerscrim{position:absolute;left:0;right:0;bottom:0;height:230px;background:
    linear-gradient(to top, rgba(4,6,12,.88) 0%, rgba(4,6,12,.45) 45%, transparent 100%)}
  .vignette{position:absolute;inset:0;background:
    radial-gradient(140% 120% at 50% 50%, transparent 55%, rgba(0,0,0,.55) 100%);}

  /* code-drawn fallback art */
  .motif{position:absolute;inset:0;width:var(--w);height:var(--h)}
  .motif .edges path{fill:none;stroke:rgba(125,205,255,.30);stroke-width:2.5;
    stroke-linecap:round;marker-end:url(#arrow)}
  .motif .edges path.lit{stroke:rgba(34,211,238,.72);stroke-width:3.5}
  .motif .satellites circle{fill:rgba(125,205,255,.42)}
  .motif .node rect{fill:rgba(8,16,30,.74);stroke:rgba(125,205,255,.34);stroke-width:2}
  .motif .node .bar{fill:none;stroke:rgba(148,180,220,.42);stroke-width:6;stroke-linecap:round}
  .motif .node.on rect{stroke:rgba(34,211,238,.85);fill:rgba(10,26,42,.85)}
  .motif .node.on .bar{stroke:rgba(34,211,238,.95)}

  .ghost{position:absolute;right:${R.ghostRight}px;bottom:${R.ghostBottom}px;
    font:900 ${R.ghostSize}px/1 "Segoe UI",sans-serif;
    letter-spacing:-.06em;color:transparent;-webkit-text-stroke:2px rgba(148,197,255,.13);
    opacity:.5;user-select:none}

  .tick{position:absolute;width:${R.tick}px;height:${R.tick}px;border:2px solid rgba(34,211,238,.50)}
  .tick.tl{left:${R.tick}px;top:${R.tick}px;border-right:0;border-bottom:0}
  .tick.br{right:${R.tick}px;bottom:${R.tick}px;border-left:0;border-top:0}

  /* ---- content ---- */
  .content{position:absolute;left:var(--pad);top:292px;width:${R.contentW}px}

  .eyebrow{display:flex;align-items:center;gap:18px;margin-bottom:36px}
  .eyebrow .bar{width:56px;height:4px;background:var(--accent);
    box-shadow:0 0 22px rgba(34,211,238,.85)}
  .eyebrow .txt{font:600 25px/1 "Microsoft YaHei",sans-serif;letter-spacing:.28em;color:#B9CBE2}

  .epno{display:flex;align-items:baseline;gap:20px;margin-bottom:24px}
  .epno .n{font:600 27px/1 "Cascadia Code",Consolas,monospace;letter-spacing:.34em;
    color:var(--accent)}
  .epno .rule{flex:1;height:1px;background:linear-gradient(90deg,
    rgba(34,211,238,.55), rgba(148,180,220,.06));max-width:420px}

  /* fixed-height box keeps every card in the series on the same baseline grid */
  .titlebox{min-height:152px;display:flex;align-items:flex-start}
  h1.title{font:900 ${R.titleSize}px/1.12 "Microsoft YaHei",sans-serif;letter-spacing:-.02em;
    color:var(--ink);text-shadow:0 8px 40px rgba(0,0,0,.6);max-width:${R.titleMax}px;
    text-wrap:balance}
  h1.title .nw{white-space:nowrap}

  .en{margin-top:30px;font:600 30px/1 "Cascadia Code",Consolas,monospace;
    letter-spacing:.30em;color:#7F97B4}

  .tags{display:flex;flex-wrap:wrap;gap:14px;margin-top:44px}
  .tag{font:600 24px/1 "Microsoft YaHei",sans-serif;color:#CFE0F2;
    padding:13px 24px;border:1px solid rgba(148,180,220,.30);border-radius:999px;
    background:rgba(12,20,36,.55)}

  /* ---- footer ---- */
  .footer{position:absolute;left:var(--pad);right:var(--pad);bottom:74px;display:flex;
    align-items:center;justify-content:space-between;
    padding-top:26px;border-top:1px solid rgba(148,180,220,.18)}
  .footer .l{display:flex;align-items:center;gap:20px;font:600 24px/1 "Microsoft YaHei",sans-serif;
    color:#8FA6C0;letter-spacing:.06em}
  .footer .l .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);
    box-shadow:0 0 14px rgba(34,211,238,.9)}
  .footer .r{font:600 24px/1 "Cascadia Code",Consolas,monospace;letter-spacing:.22em;color:#5F7C9E}
</style></head>
<body>
  <div class="poster">
    <div class="bg"></div>
    ${hasBg ? '' : motifSvg(card.key, R)}
    <div class="scrim"></div>
    <div class="grid"></div>
    <div class="footerscrim"></div>
    <div class="vignette"></div>
    <div class="ghost">${esc(card.ghost ?? '')}</div>
    <div class="tick tl"></div>
    <div class="tick br"></div>

    <div class="content">
      <div class="eyebrow"><span class="bar"></span><span class="txt">${esc(card.eyebrow)}</span></div>
      <div class="epno"><span class="n">${esc(card.epLabel)}</span><span class="rule"></span></div>
      <div class="titlebox"><h1 class="title">${titleHtml(card.title)}</h1></div>
      <div class="en">${esc(card.en)}</div>
      <div class="tags">${tags}</div>
    </div>

    <div class="footer">
      <div class="l"><span class="dot"></span><span>${esc(card.footerLeft)}</span></div>
      <div class="r">${esc(card.footerRight)}</div>
    </div>
  </div>
  <script>
    // Auto-fit the title: shrink until it fits the fixed box (at most two lines) and
    // stops overflowing sideways, so long and short names share one series scale.
    (function () {
      var el = document.querySelector('.title');
      var size = parseFloat(getComputedStyle(el).fontSize);
      var limit = 296;
      function tooBig() {
        return el.getBoundingClientRect().height > limit || el.scrollWidth > el.clientWidth + 1;
      }
      while (tooBig() && size > 52) { size -= 2; el.style.fontSize = size + 'px'; }
    })();
  </script>
</body></html>`;
}

function shoot(htmlPath, pngPath, R) {
  execFileSync(BROWSER, [
    '--headless=new',
    '--disable-gpu',
    '--hide-scrollbars',
    '--force-device-scale-factor=1',
    '--allow-file-access-from-files',
    `--window-size=${R.w},${R.h}`,
    '--virtual-time-budget=4000',
    `--screenshot=${pngPath}`,
    fileUrl(htmlPath),
  ], { stdio: 'ignore' });
}

const only = process.argv.slice(2);
fs.mkdirSync(OUT_DIR, { recursive: true });
fs.mkdirSync(BG_DIR, { recursive: true });
fs.mkdirSync(HTML_DIR, { recursive: true });

for (const card of cards) {
  if (only.length && !only.includes(card.key)) continue;
  const art = fs.existsSync(path.join(BG_DIR, `${card.key}.png`)) ? 'ai' : 'vector';
  for (const R of RATIOS) {
    const dir = path.join(OUT_DIR, R.key);
    fs.mkdirSync(dir, { recursive: true });
    const htmlPath = path.join(HTML_DIR, `${card.key}-${R.key}.html`);
    fs.writeFileSync(htmlPath, cardHtml(card, R), 'utf8');
    const pngPath = path.join(dir, `${card.key}.png`);
    shoot(htmlPath, pngPath, R);
    console.log(`${card.key} [${R.key}] -> ${pngPath}  art=${art}`);
  }
}
console.log('root=' + ROOT);
