#!/usr/bin/env node
/**
 * pack-evidence.mjs — keep a pitfalls guide's source evidence in ONE small, traceable archive.
 *
 * A pitfall usually needs only a slice of a long transcript, not the whole file. This script cuts
 * that slice out and appends it to a single gzipped archive (`<guide>/evidence.gz`) instead of
 * scattering one file per pitfall: the archive stays a few tens of KB, is greppable once unpacked,
 * and one section can still be printed without writing anything to disk.
 *
 * Usage (paths relative to this skill directory):
 *   node scripts/pack-evidence.mjs add    --guide <dir> --id VER-02 --title "..." \
 *                                         --segment <session.md>:7185-7215 [--segment ...]
 *   node scripts/pack-evidence.mjs list   --guide <dir>
 *   node scripts/pack-evidence.mjs show   --guide <dir> --id VER-02
 *   node scripts/pack-evidence.mjs unpack --guide <dir> [--out <file>]
 *   node scripts/pack-evidence.mjs check  --guide <dir> [--budget-mb 8]
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';

// Deliberately a plain `.gz`, not `*.md.gz`: a markdown-ish double suffix invites editors, web UIs
// and code review tools to treat a binary archive as a document. The tool owns how it is read, so
// the extension should not claim a content type.
const ARCHIVE_NAME = 'evidence.gz';
const DEFAULT_BUDGET_MB = 8;
const SECTION_MARK = /^<!-- section:([A-Za-z]+-?\d+) -->$/m;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function safeStat(p) {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

function humanBytes(n) {
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`;
  if (n > 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');

function archivePath(guide) {
  return path.join(guide, ARCHIVE_NAME);
}

/** Read a contiguous, 1-based inclusive line range out of a session export. */
function readSlice(file, from, to) {
  const raw = (() => {
    try {
      return fs.readFileSync(file);
    } catch {
      return null;
    }
  })();
  if (!raw) throw new Error(`cannot read session file: ${file}`);
  if (!(from >= 1 && to >= from)) throw new Error('invalid line range');
  const lines = raw.toString('utf8').split('\n');
  if (from > lines.length) {
    throw new Error(`start line ${from} exceeds file length ${lines.length} in ${path.basename(file)}`);
  }
  // Join the SLICE, not the whole file: joining `lines` here once archived megabytes while still
  // reporting the excerpt's line count.
  const slice = lines.slice(from - 1, Math.min(to, lines.length));
  if (slice.length === 0) throw new Error('line range is empty');
  return { text: slice.join('\n'), lineCount: slice.length };
}

// ---------------------------------------------------------------------------
// archive format
//
//   # Evidence archive
//   <index table>
//
//   <!-- section:VER-02 -->
//   <a id="ver-02"></a>
//   ## VER-02 · title
//   `pitfall` … · `segments` 1 · `added` …
//   ### Segment 1 · <session> lines a–b (n lines)
//   - Original export: …
//   - sha256: …
//   <excerpt>
// ---------------------------------------------------------------------------

function emptyArchive() {
  return { sections: new Map() };
}

function parseArchive(text) {
  const sections = new Map();
  if (!text) return emptyArchive();
  const parts = text.split(SECTION_MARK);
  // parts[0] is the preamble; then id, body, id, body, …
  for (let i = 1; i + 1 < parts.length; i += 2) {
    const id = parts[i];
    sections.set(id, parts[i + 1].replace(/\n+$/, '\n'));
  }
  return { sections };
}

function readArchive(guide) {
  const buf = (() => {
    try {
      return fs.readFileSync(archivePath(guide));
    } catch {
      return null;
    }
  })();
  if (!buf) return emptyArchive();
  try {
    return parseArchive(zlib.gunzipSync(buf).toString('utf8'));
  } catch (error) {
    throw new Error(`cannot read ${ARCHIVE_NAME}: ${error.message}`, { cause: error });
  }
}

/** Section metadata is read back out of the rendered text, so the archive stays the single source. */
function sectionInfo(id, body) {
  const title = /^##\s+[^\n·]*·\s*(.+)$/m.exec(body)?.[1]?.trim() ?? id;
  const segments = body.match(/^### Segment /gm)?.length ?? 0;
  const sessions = [...body.matchAll(/^### Segment \d+ · (\S+) lines/gm)].map((m) => m[1]);
  const ranges = [...body.matchAll(/^### Segment \d+ · \S+ lines (\d+)–(\d+)/gm)].map((m) => `${m[1]}-${m[2]}`);
  return { id, title, segments, sessions: [...new Set(sessions)], ranges };
}

function renderArchive({ sections }) {
  const infos = [...sections.keys()].toSorted().map((id) => sectionInfo(id, sections.get(id)));
  const head = [
    '# Evidence archive',
    '',
    `- Sections: ${infos.length}`,
    `- Updated: ${new Date().toISOString()}`,
    '- Kept in one file on purpose: one section per pitfall, still greppable after `unpack`.',
    '- Print a single section without unpacking: `pack-evidence.mjs show --id <ID>`',
    '',
    '| ID | Pitfall | Segments | Session lines |',
    '| --- | --- | --- | --- |',
    ...infos.map(
      (i) => `| ${i.id} | ${i.title.replace(/\|/g, '\\|')} | ${i.segments} | ${i.ranges.join(', ') || '—'} |`,
    ),
    '',
  ].join('\n');

  const body = infos
    .map((i) => `<!-- section:${i.id} -->\n${sections.get(i.id).replace(/^\n+/, '')}`)
    .join('\n');
  return `${head}\n${body}`;
}

function writeArchive(guide, archive) {
  fs.mkdirSync(guide, { recursive: true });
  const text = renderArchive(archive);
  fs.writeFileSync(archivePath(guide), zlib.gzipSync(Buffer.from(text, 'utf8'), { level: 9 }));
  return text;
}

/** Directory footprint, archive included — the number that decides whether the guide bloats. */
function measure(guide) {
  let prose = 0;
  let packed = 0;
  for (const entry of (() => {
    try {
      return fs.readdirSync(guide, { withFileTypes: true });
    } catch {
      return [];
    }
  })()) {
    if (!entry.isFile()) continue;
    const size = safeStat(path.join(guide, entry.name))?.size ?? 0;
    if (entry.name.endsWith('.gz')) packed += size;
    else prose += size;
  }
  return { prose, packed, total: prose + packed };
}

// ---------------------------------------------------------------------------
// commands
// ---------------------------------------------------------------------------

function cmdAdd(args) {
  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const id = String(args.id ?? '').trim().toUpperCase();
  if (!/^[A-Z]+-?\d+$/.test(id)) throw new Error('--id must look like VER-02 or P008');

  // One pitfall often needs several non-contiguous excerpts, sometimes from different sessions.
  const requested = [];
  for (const spec of [].concat(args.segment ?? [])) {
    const m = /^(.+):(\d+)\s*-\s*(\d+)$/.exec(String(spec).trim());
    if (!m) throw new Error(`--segment must look like <session-file>:5650-5720 (got "${spec}")`);
    requested.push({ file: path.resolve(m[1]), from: Number(m[2]), to: Number(m[3]) });
  }
  if (requested.length === 0) {
    if (!args.session) {
      throw new Error('missing one or more --segment <file>:<from>-<to> (or --session/--lines for a single one)');
    }
    if (!args.lines) throw new Error('missing --lines <from>-<to>');
    const m = /^(\d+)\s*-\s*(\d+)$/.exec(String(args.lines).trim());
    if (!m) throw new Error('--lines must look like 5650-5720');
    requested.push({ file: path.resolve(String(args.session)), from: Number(m[1]), to: Number(m[2]) });
  }

  const segments = requested.map((req) => {
    const { text, lineCount } = readSlice(req.file, req.from, req.to);
    return {
      session: path.basename(req.file).replace(/\.(md|jsonl)$/, ''),
      lines: [req.from, req.from + lineCount - 1],
      lineCount,
      text,
      sha256: sha(text),
    };
  });

  const title = args.title ?? id;
  const lines = [
    `<a id="${id.toLowerCase()}"></a>`,
    '',
    `## ${id} · ${title}`,
    '',
    `\`pitfall\` ${title} · \`segments\` ${segments.length} · \`added\` ${new Date().toISOString().slice(0, 10)}`,
    args.anchor ? ` · \`anchor\` ${args.anchor}` : '',
    '',
  ].join('\n');

  const body = segments
    .map((s, i) =>
      [
        `### Segment ${i + 1} · ${s.session} lines ${s.lines[0]}–${s.lines[1]} (${s.lineCount} lines)`,
        `- sha256: ${s.sha256}`,
        '',
        s.text,
        '',
      ].join('\n'),
    )
    .join('');

  const archive = readArchive(guide);
  archive.sections.set(id, `\n${lines}${body}`);
  const text = writeArchive(guide, archive);

  // The archive travels with the repository, so a personal path inside it is a dead reference for
  // everyone else and a privacy leak for the author. Warn loudly rather than shipping it silently.
  const personal = [...body.matchAll(/(?:[A-Za-z]:\\Users\\[^\\\s`"')]+|\/Users\/[^/\s`"')]+)/g)].map((m) => m[0]);
  if (personal.length > 0) {
    process.stderr.write(
      `⚠ excerpt contains ${personal.length} personal path(s), e.g. ${personal[0]} — ` +
        'the archive ships with the repo. Rewrite as `~/…` (or confirm this is intended).\n',
    );
  }

  const raw = Buffer.byteLength(text, 'utf8');
  const packedBytes = safeStat(archivePath(guide))?.size ?? 0;
  if (raw > 512 * 1024) {
    process.stderr.write(
      `⚠ "${id}" makes the archive ${humanBytes(raw)} uncompressed — trim the line ranges to the passage that matters.\n`,
    );
  }

  const s = measure(guide);
  process.stdout.write(
    `wrote ${path.relative(process.cwd(), archivePath(guide))}  ` +
      `(${humanBytes(raw)} → ${humanBytes(packedBytes)}, ${((packedBytes / raw) * 100).toFixed(0)}%)\n` +
      `guide size: prose ${humanBytes(s.prose)} + archive ${humanBytes(s.packed)} = ${humanBytes(s.total)}\n`,
  );

  const budgetMb = Number(args['budget-mb'] ?? DEFAULT_BUDGET_MB);
  if (s.total > budgetMb * 1024 * 1024) {
    process.stderr.write(
      `⚠ over the ${budgetMb} MB budget. Trim excerpts or line ranges rather than archiving whole sessions.\n`,
    );
    process.exitCode = 2;
  }
}

function cmdRemove(args) {
  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const ids = [].concat(args.id ?? []).map((s) => String(s).trim().toUpperCase());
  if (ids.length === 0) throw new Error('missing --id <ID> (repeatable)');
  const archive = readArchive(guide);
  const removed = ids.filter((id) => archive.sections.delete(id));
  if (removed.length === 0) {
    process.stderr.write(`⚠ nothing removed (no such section: ${ids.join(', ')})\n`);
    process.exitCode = 1;
    return;
  }
  writeArchive(guide, archive);
  process.stdout.write(`Removed ${removed.length} section(s): ${removed.join(', ')}\n`);
}

function cmdList(args) {
  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const { sections } = readArchive(guide);
  if (sections.size === 0) {
    process.stdout.write('No archived evidence yet.\n');
    return;
  }
  const infos = [...sections.keys()].toSorted().map((id) => sectionInfo(id, sections.get(id)));
  process.stdout.write(`archive: ${path.relative(process.cwd(), archivePath(guide))}\n\n`);
  for (const i of infos) {
    process.stdout.write(`${i.id.padEnd(11)} seg=${String(i.segments).padStart(2)}  ${i.ranges.join(', ').padEnd(18)} ${i.title}\n`);
  }
  const s = measure(guide);
  process.stdout.write(
    `\n${infos.length} section(s); prose ${humanBytes(s.prose)}, archive ${humanBytes(s.packed)}, ` +
      `total ${humanBytes(s.total)}\n`,
  );
}

function cmdShow(args) {
  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const id = String(args.id ?? '').trim().toUpperCase();
  const { sections } = readArchive(guide);
  const body = sections.get(id);
  if (!body) {
    process.stderr.write(`⚠ no such section: ${id}\n`);
    process.exitCode = 1;
    return;
  }
  process.stdout.write(`<!-- section:${id} -->${body}`);
}

function cmdUnpack(args) {
  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const out = path.resolve(args.out ?? path.join(os.tmpdir(), `pitfall-evidence-${Date.now()}.md`));
  const text = renderArchive(readArchive(guide));
  fs.writeFileSync(out, text, 'utf8');
  process.stdout.write(`Unpacked ${humanBytes(Buffer.byteLength(text, 'utf8'))} to ${out}\n`);
}

function cmdCheck(args) {
  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const budgetMb = Number(args['budget-mb'] ?? DEFAULT_BUDGET_MB);
  const { sections } = readArchive(guide);
  const s = measure(guide);
  const lines = [
    `guide: ${path.relative(process.cwd(), guide).replace(/\\/g, '/')}`,
    `  prose        ${humanBytes(s.prose)}`,
    `  evidence     ${humanBytes(s.packed)} (gzipped, ${sections.size} section(s))`,
    `  total        ${humanBytes(s.total)}  / budget ${budgetMb} MB`,
  ];
  if (s.total > budgetMb * 1024 * 1024) {
    lines.push('  ⚠ over budget: trim line ranges or drop sections that are no longer referenced');
    process.exitCode = 2;
  } else {
    lines.push(`  headroom     ${humanBytes(budgetMb * 1024 * 1024 - s.total)}`);
  }
  process.stdout.write(`${lines.join('\n')}\n`);
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const args = { _: [] };
  // Repeatable flags (--segment may appear many times) collect into an array.
  const assign = (key, value) => {
    if (args[key] === undefined) args[key] = value;
    else if (Array.isArray(args[key])) args[key].push(value);
    else args[key] = [args[key], value];
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) assign(key, true);
      else {
        assign(key, next);
        i += 1;
      }
    } else args._.push(a);
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0] ?? 'check';
  try {
    if (command === 'add') cmdAdd(args);
    else if (command === 'remove') cmdRemove(args);
    else if (command === 'list') cmdList(args);
    else if (command === 'show') cmdShow(args);
    else if (command === 'unpack') cmdUnpack(args);
    else if (command === 'check') cmdCheck(args);
    else {
      process.stderr.write(`Unknown command: ${command}\nUsage: add | remove | list | show | unpack | check\n`);
      process.exitCode = 1;
    }
  } catch (error) {
    process.stderr.write(`Error: ${error.message}\n`);
    process.exitCode = 1;
  }
}

main();
