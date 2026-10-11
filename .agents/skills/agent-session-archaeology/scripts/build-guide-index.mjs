#!/usr/bin/env node
/**
 * build-guide-index.mjs — regenerate a pitfalls guide's index from the topic files themselves.
 *
 * The guide is meant to grow in batches, so nothing here is hand-maintained: it reads every
 * `NN-*.md` topic file, parses the pitfall records, and rewrites the region between the
 * `<!-- BEGIN:auto-index -->` / `<!-- END:auto-index -->` markers in README.md.
 *
 * Usage (paths relative to this skill directory):
 *   node scripts/build-guide-index.mjs --guide <dir> [--check]
 *
 * `--check` reports drift without writing (exit 1 when README is stale) — useful before a commit.
 */

import fs from 'node:fs';
import path from 'node:path';

const BEGIN = '<!-- BEGIN:auto-index -->';
const END = '<!-- END:auto-index -->';
const TOPIC_RE = /^(\d{2})-(.+)\.md$/;
const PITFALL_RE = /^###\s+([A-Za-z]+-\d+)\s*·\s*(.+?)\s*$/;
/**
 * Metadata labels are machine-read keys, so they stay English even when the guide body is written
 * in another language. Add your language's spellings here if you want localized labels.
 */
const FIELD_ALIASES = {
  batch: ['batch'],
  severity: ['severity'],
  status: ['status'],
  source: ['source'],
};

function fieldValue(meta, key) {
  for (const alias of FIELD_ALIASES[key]) {
    const m = new RegExp(`\`${alias}\`\\s*([^·\\n]+)`).exec(meta);
    if (m) return m[1].trim();
  }
  return '—';
}

function hasField(meta, key) {
  return FIELD_ALIASES[key].some((alias) => new RegExp(`\`${alias}\``).test(meta));
}

function safeReaddir(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

function escapeCell(s) {
  return String(s).replace(/\|/g, '\\|').trim();
}

/** Parse one topic file into its heading, blurb and pitfall records. */
function parseTopic(file) {
  const text = fs.readFileSync(file, 'utf8');
  const lines = text.split('\n');

  let title = null;
  let blurb = null;
  let inFrontmatterish = true;
  for (const line of lines) {
    if (!title && line.startsWith('# ')) {
      title = line.slice(2).trim().replace(/^\d+\s*·\s*/, '');
      continue;
    }
    if (title && inFrontmatterish) {
      if (line.trim().startsWith('>')) {
        const t = line.replace(/^>\s?/, '').trim();
        if (t && !blurb) blurb = t;
      } else if (line.trim() && !line.startsWith('`') && !line.startsWith('#')) {
        inFrontmatterish = false;
      }
    }
  }

  const pitfalls = [];
  for (let i = 0; i < lines.length; i += 1) {
    const m = PITFALL_RE.exec(lines[i]);
    if (!m) continue;
    const [, id, name] = m;
    // Metadata sits within the next few lines, before the first bullet.
    let meta = '';
    for (let j = i + 1; j < Math.min(i + 6, lines.length); j += 1) {
      if (lines[j].startsWith('- ')) break;
      if (lines[j].trim()) {
        meta += ` ${lines[j]}`;
        if (hasField(meta, 'source')) break;
      }
    }
    const sev = fieldValue(meta, 'severity');
    const status = fieldValue(meta, 'status');
    const batch = fieldValue(meta, 'batch');
    // Kept raw: it is a comma/`·`-joined list of `tool/session @lines` references, and incremental
    // updates deduplicate against it.
    const source = fieldValue(meta, 'source');
    const hasEvidence = /📎/.test(lines.slice(i, Math.min(i + 80, lines.length)).join('\n').split('\n---')[0]);
    pitfalls.push({ id, name: name.trim(), severity: sev, status, batch, source, hasEvidence });
  }

  return { title: title ?? path.basename(file, '.md'), blurb, pitfalls };
}

/** Highest used number per prefix, +1 — so a new pitfall continues the sequence instead of colliding. */
function nextIds(all) {
  const highest = new Map();
  for (const p of all) {
    const m = /^([A-Z]+)-(\d+)$/.exec(p.id);
    if (!m) continue;
    const n = Number(m[2]);
    if (!highest.has(m[1]) || n > highest.get(m[1])) highest.set(m[1], n);
  }
  return Object.fromEntries(
    [...highest]
      .toSorted((a, b) => a[0].localeCompare(b[0]))
      .map(([prefix, n]) => [prefix, `${prefix}-${String(n + 1).padStart(2, '0')}`]),
  );
}

function collect(guide) {
  const files = safeReaddir(guide)
    .filter((e) => e.isFile() && TOPIC_RE.test(e.name))
    .map((e) => e.name)
    .toSorted();
  const topics = [];
  for (const name of files) {
    const parsed = parseTopic(path.join(guide, name));
    topics.push({ file: name, ...parsed });
  }
  const all = topics.flatMap((t) => t.pitfalls.map((p) => ({ ...p, topic: t.title, file: t.file })));
  return { topics, all };
}

function renderIndex({ topics, all }) {
  const out = [];
  const batches = [...new Set(all.map((p) => p.batch).filter((b) => b !== '—'))].toSorted();

  out.push('## Topic index', '');
  out.push('| Topic | Pitfalls | Coverage |', '| --- | --- | --- |');
  for (const t of topics) {
    if (t.pitfalls.length === 0) continue;
    out.push(`| [${escapeCell(t.title)}](./${t.file}) | ${t.pitfalls.length} | ${escapeCell(t.blurb ?? '')} |`);
  }
  out.push('', `**${all.length} pitfalls across ${topics.filter((t) => t.pitfalls.length > 0).length} topics.**`, '');

  out.push('## All pitfalls', '');
  out.push('| ID | Pitfall | Topic | Severity | Status |', '| --- | --- | --- | --- | --- |');
  for (const p of all.toSorted((a, b) => a.id.localeCompare(b.id))) {
    const anchor = p.id.toLowerCase();
    out.push(
      `| [${p.id}](./${p.file}#${anchor}) | ${escapeCell(p.name)} | ${escapeCell(p.topic)} | ${p.severity} | ${p.status} |`,
    );
  }
  out.push('');

  const bySeverity = {};
  for (const p of all) bySeverity[p.severity] = (bySeverity[p.severity] ?? 0) + 1;
  const byStatus = {};
  for (const p of all) byStatus[p.status] = (byStatus[p.status] ?? 0) + 1;

  // Generated labels stay English so the tooling itself carries no local language; the rows it
  // emits keep whatever language the guide body uses.
  out.push('## Statistics', '');
  out.push(`- Total: **${all.length}**`);
  out.push(`- Batches: ${batches.length ? batches.map((b) => `\`${b}\``).join(', ') : '(none)'}`);
  out.push(`- Severity: ${Object.entries(bySeverity).map(([k, v]) => `${k} ${v}`).join(' · ')}`);
  out.push(`- Status: ${Object.entries(byStatus).map(([k, v]) => `${k} ${v}`).join(' · ')}`);
  out.push(`- With archived evidence: ${all.filter((p) => p.hasEvidence).length}`);
  out.push('');
  return out.join('\n');
}

function main() {
  const args = (() => {
    const a = { _: [] };
    const argv = process.argv.slice(2);
    for (let i = 0; i < argv.length; i += 1) {
      if (argv[i].startsWith('--')) {
        const key = argv[i].slice(2);
        const next = argv[i + 1];
        if (next === undefined || next.startsWith('--')) a[key] = true;
        else {
          a[key] = next;
          i += 1;
        }
      } else a._.push(argv[i]);
    }
    return a;
  })();

  const guide = path.resolve(args.guide ?? 'docs/avoiding-pitfalls-guide');
  const readme = path.join(guide, 'README.md');
  const data = collect(guide);

  // Machine-readable inventory for incremental updates: an agent deduplicates new findings against
  // `source`/`id` and reads the next free id, instead of re-reading every topic file.
  if (args.json) {
    process.stdout.write(
      `${JSON.stringify(
        {
          guide: path.relative(process.cwd(), guide).replace(/\\/g, '/'),
          topics: data.topics.map((t) => ({ file: t.file, title: t.title, pitfalls: t.pitfalls.length })),
          existingIds: data.all.map((p) => p.id).toSorted(),
          nextIds: nextIds(data.all),
          pitfalls: data.all,
        },
        null,
        2,
      )}\n`,
    );
    return;
  }

  const generated = renderIndex(data);

  if (data.all.length === 0) {
    process.stderr.write(`No pitfall records found under ${guide} (expected NN-*.md files with "### ID · title" headings).\n`);
    process.exitCode = 1;
    return;
  }

  const existing = (() => {
    try {
      return fs.readFileSync(readme, 'utf8');
    } catch {
      return null;
    }
  })();

  if (existing === null) {
    process.stderr.write(`README.md not found: ${readme}\nCreate it with a ${BEGIN} / ${END} marker pair first.\n`);
    process.exitCode = 1;
    return;
  }

  const b = existing.indexOf(BEGIN);
  const e = existing.indexOf(END);
  if (b === -1 || e === -1 || e < b) {
    process.stderr.write(`README.md is missing the ${BEGIN} / ${END} marker pair.\n`);
    process.exitCode = 1;
    return;
  }

  const next = `${existing.slice(0, b + BEGIN.length)}\n${generated}${existing.slice(e)}`;
  if (next === existing) {
    process.stdout.write(`Index already up to date (${data.all.length} pitfalls across ${data.topics.length} topics).\n`);
    return;
  }
  if (args.check) {
    process.stderr.write(`README index is stale; re-run build-guide-index.mjs to regenerate it.\n`);
    process.exitCode = 1;
    return;
  }
  fs.writeFileSync(readme, next, 'utf8');
  process.stdout.write(`Index updated: ${data.all.length} pitfalls across ${data.topics.length} topic files.\n`);
}

main();
