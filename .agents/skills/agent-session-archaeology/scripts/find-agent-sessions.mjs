#!/usr/bin/env node
/**
 * find-agent-sessions.mjs — locate every local coding-agent conversation that belongs to one project.
 *
 * Portable by design: no hard-coded user, machine, drive letter or project path. Works on
 * Windows, macOS and Linux, with any project, and degrades gracefully when a source is unavailable.
 *
 * Why this exists: every agent tool keeps its transcripts somewhere else, in its own format, and
 * several of them will happily hand you a *partial* transcript if you read them the obvious way.
 * No two tools agree on layout, so each source is a separate adapter.
 *
 * Sources:
 *   claude-code  ~/.claude/projects/<slug>/<uuid>.jsonl       plain JSONL, cwd on every message record
 *                ~/.claude/history.jsonl                     global prompt history, carries `project`
 *   dsh          ~/.dsh/storages/workspace.json               AUTHORITATIVE path -> sessionIds registry
 *                ~/.dsh/sessions/<slug>/<session>/session.v<N>.jsonl.zstd
 *                                                             multi-frame zstd; one frame per append
 *   codex        ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl session_meta.cwd on line 1
 *   copilot      <appData>/<product>/User/workspaceStorage/<hash>/workspace.json
 *                                                             folder URI -> chatSessions/*.jsonl
 *
 * Usage (paths relative to this skill directory):
 *   node scripts/find-agent-sessions.mjs scan   [--project <dir>] [--json] [--content]
 *   node scripts/find-agent-sessions.mjs export [--project <dir>] [--out <dir>] [--full] [--content]
 *   node scripts/find-agent-sessions.mjs doctor
 *
 * `export` redacts credential values out of what it wrote (see redact-exports.mjs); an export
 * directory that was produced some other way can be cleaned with that script directly.
 *
 * Adding a new tool: write a reader returning the shared session shape, then add one entry to
 * SOURCES below. Nothing else needs to change.
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';

import { redactDirectory } from './redact-exports.mjs';

// ---------------------------------------------------------------------------
// environment
// ---------------------------------------------------------------------------

const HOME = os.homedir();

/** zstd landed in Node 23.8 as `zlib.zstdDecompressSync`. Older runtimes lose one source, not the tool. */
const HAS_ZSTD = typeof zlib.zstdDecompressSync === 'function';

/**
 * Application-data roots, platform-first but with every convention offered: WSL, containers and
 * CI images routinely keep data somewhere other than the "native" location, and probing is cheap.
 */
function appDataRoots() {
  const roots = [];
  const push = (p) => {
    if (p && !roots.includes(p)) roots.push(p);
  };
  if (process.platform === 'win32') push(process.env.APPDATA);
  else if (process.platform === 'darwin') push(path.join(HOME, 'Library', 'Application Support'));
  else push(process.env.XDG_CONFIG_HOME || path.join(HOME, '.config'));

  push(process.env.APPDATA);
  push(process.env.XDG_CONFIG_HOME || path.join(HOME, '.config'));
  push(path.join(HOME, 'Library', 'Application Support'));
  return roots;
}

const APP_DATA_ROOTS = appDataRoots();

// ---------------------------------------------------------------------------
// paths
// ---------------------------------------------------------------------------

/**
 * Absolute form, used for both comparison and display.
 *
 * `path.resolve` prepends the current drive letter to a POSIX-absolute path on Windows, which
 * misreports paths that originated on another platform (shared configs, WSL, copied transcripts).
 * Those are already absolute, so leave them alone.
 */
function absolute(p) {
  const s = String(p);
  if (process.platform === 'win32' && s.startsWith('/')) return s;
  try {
    return path.resolve(s);
  } catch {
    return s;
  }
}

/** Normalize for comparison: absolute, forward slashes, no trailing separator, lowercase. */
function norm(p) {
  return absolute(p).replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

/** True when `cwd` is the project directory itself or anything inside it. */
function within(cwd, projectNorm) {
  if (!cwd) return false;
  const c = norm(cwd);
  return c === projectNorm || c.startsWith(`${projectNorm}/`);
}

function safeReaddir(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

function safeStat(p) {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

function safeRead(p) {
  try {
    return fs.readFileSync(p);
  } catch {
    return null;
  }
}

function exists(p) {
  return safeStat(p) !== null;
}

function walkFiles(dir, test, out = [], depth = 0) {
  if (depth > 6) return out;
  for (const entry of safeReaddir(dir)) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walkFiles(full, test, out, depth + 1);
    else if (entry.isFile() && test(full)) out.push(full);
  }
  return out;
}

// ---------------------------------------------------------------------------
// decoding
// ---------------------------------------------------------------------------

/**
 * Decode a zstd file that may hold SEVERAL concatenated frames (DSH appends one frame per write).
 * `zlib.zstdDecompressSync` stops after the first frame and reports no error, so a whole-buffer
 * one-shot call silently returns just the session header. Split on the frame magic instead.
 */
function decodeZstdFile(file) {
  if (!HAS_ZSTD) throw new Error('zstd support missing (needs Node >= 23.8)');
  const buf = safeRead(file);
  if (!buf || buf.length === 0) return '';
  const offsets = [];
  for (let i = 0; i + 4 <= buf.length; i += 1) {
    if (buf[i] === 0x28 && buf[i + 1] === 0xb5 && buf[i + 2] === 0x2f && buf[i + 3] === 0xfd) offsets.push(i);
  }
  const parts = [];
  const attempt = (b) => {
    try {
      parts.push(zlib.zstdDecompressSync(b));
      return true;
    } catch {
      return false;
    }
  };
  if (offsets.length > 1) for (const off of offsets) attempt(buf.subarray(off));
  else if (!attempt(buf)) for (const off of offsets) attempt(buf.subarray(off));
  return Buffer.concat(parts).toString('utf8');
}

function parseJsonl(text) {
  const rows = [];
  for (const line of text.split('\n')) {
    const s = line.trim();
    if (!s) continue;
    try {
      rows.push(JSON.parse(s));
    } catch {
      /* truncated tail or non-JSON line — ignore */
    }
  }
  return rows;
}

function isoToMs(v) {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string') {
    const t = Date.parse(v);
    if (Number.isFinite(t)) return t;
  }
  return null;
}

// ---------------------------------------------------------------------------
// shared conversation model
//   session = { source, id, file, cwd, title, startedAt, endedAt, messages, note }
//   message = { role: 'user'|'assistant'|'tool'|'system', time, text, tool?, result? }
// ---------------------------------------------------------------------------

function makeSession(source, id, file, extra = {}) {
  return {
    source,
    id,
    file,
    cwd: null,
    title: null,
    startedAt: null,
    endedAt: null,
    messages: [],
    note: null,
    ...extra,
  };
}

function stamp(session, time) {
  const t = isoToMs(time);
  if (t === null) return;
  if (session.startedAt === null || t < session.startedAt) session.startedAt = t;
  if (session.endedAt === null || t > session.endedAt) session.endedAt = t;
}

/** Flatten a Claude-style content block list into text plus tool call/result records. */
function flattenBlocks(content) {
  const text = [];
  const tools = [];
  const results = [];
  if (typeof content === 'string') text.push(content);
  else if (Array.isArray(content)) {
    for (const b of content) {
      if (!b || typeof b !== 'object') continue;
      if (b.type === 'text' && typeof b.text === 'string') text.push(b.text);
      else if (b.type === 'tool_use') tools.push({ name: b.name, input: b.input });
      else if (b.type === 'tool_result') results.push(flattenBlocks(b.content).text);
      else if (b.type === 'input_text' && typeof b.text === 'string') text.push(b.text);
      else if (b.type === 'output_text' && typeof b.text === 'string') text.push(b.text);
    }
  }
  return { text: text.join('\n').trim(), tools, results: results.filter(Boolean) };
}

// ---------------------------------------------------------------------------
// adapter: claude-code  (~/.claude)
// ---------------------------------------------------------------------------

const CLAUDE_ROOT = path.join(HOME, '.claude');

function readClaudeCode() {
  const root = path.join(CLAUDE_ROOT, 'projects');
  const sessions = [];
  for (const dir of safeReaddir(root)) {
    if (!dir.isDirectory()) continue;
    for (const entry of safeReaddir(path.join(root, dir.name))) {
      if (!entry.isFile() || !entry.name.endsWith('.jsonl')) continue;
      const file = path.join(root, dir.name, entry.name);
      const text = safeRead(file)?.toString('utf8');
      if (!text) continue;
      const rows = parseJsonl(text);
      if (rows.length === 0) continue;
      const session = makeSession('claude-code', entry.name.replace(/\.jsonl$/, ''), file);
      for (const r of rows) {
        if (!session.cwd && typeof r.cwd === 'string') session.cwd = r.cwd;
        if (r.type === 'ai-title' && typeof r.title === 'string' && !session.title) session.title = r.title;
        if (r.type !== 'user' && r.type !== 'assistant') continue;
        const msg = r.message ?? {};
        const blocks = flattenBlocks(msg.content);
        stamp(session, r.timestamp);
        for (const tool of blocks.tools) {
          session.messages.push({ role: 'tool', time: isoToMs(r.timestamp), tool: tool.name, text: '' });
        }
        if (!blocks.text) continue;
        session.messages.push({
          role: r.type === 'assistant' ? 'assistant' : 'user',
          time: isoToMs(r.timestamp),
          text: blocks.text,
        });
      }
      if (session.messages.length > 0) sessions.push(session);
    }
  }
  return sessions;
}

/** Global prompt history: only useful as corroboration, never as a transcript. */
function readClaudeHistory(projectNorm) {
  const buf = safeRead(path.join(CLAUDE_ROOT, 'history.jsonl'));
  if (!buf) return [];
  const rows = parseJsonl(buf.toString('utf8'));
  return rows.filter((r) => typeof r.project === 'string' && norm(r.project) === projectNorm);
}

// ---------------------------------------------------------------------------
// adapter: dsh  (~/.dsh)
// ---------------------------------------------------------------------------

const DSH_ROOT = path.join(HOME, '.dsh');

/** ~/.dsh/storages/workspace.json is the tool's own path -> sessionIds map. Trust it first. */
function readDshRegistry() {
  const file = path.join(DSH_ROOT, 'storages', 'workspace.json');
  const buf = safeRead(file);
  if (!buf) return { byPath: new Map(), archived: new Set() };
  let json;
  try {
    json = JSON.parse(buf.toString('utf8'));
  } catch {
    return { byPath: new Map(), archived: new Set() };
  }
  const byPath = new Map();
  for (const ws of Object.values(json?.tables?.workspaces ?? {})) {
    if (!ws || typeof ws.path !== 'string') continue;
    byPath.set(norm(ws.path), {
      title: typeof ws.title === 'string' ? ws.title : null,
      sessionIds: new Set((ws.sessionIds ?? []).map((s) => String(s))),
    });
  }
  const archived = new Set((json?.global?.archivedSessionIds ?? []).map((s) => String(s)));
  return { byPath, archived };
}

const DSH_VERSION_RE = /^session\.v(\d+)\.jsonl\.zstd$/;

function readDsh() {
  const root = path.join(DSH_ROOT, 'sessions');
  const sessions = [];
  for (const slugDir of safeReaddir(root)) {
    if (!slugDir.isDirectory()) continue;
    for (const sessionDir of safeReaddir(path.join(root, slugDir.name))) {
      if (!sessionDir.isDirectory()) continue;
      const dirPath = path.join(root, slugDir.name, sessionDir.name);
      // One session is written as several format versions side by side; keep only the newest.
      let best = null;
      for (const f of safeReaddir(dirPath)) {
        const m = DSH_VERSION_RE.exec(f.name);
        if (!m) continue;
        const version = Number(m[1]);
        if (!best || version > best.version) best = { version, file: path.join(dirPath, f.name) };
      }
      if (!best) continue;
      const rows = parseJsonl(decodeZstdFile(best.file));
      if (rows.length === 0) continue;
      const meta = rows.find((r) => r.type === 'session') ?? {};
      const id = String(meta.id ?? sessionDir.name);
      const session = makeSession('dsh', id, best.file, { slug: slugDir.name, version: best.version });
      session.cwd = typeof meta.cwd === 'string' ? meta.cwd : null;
      if (isoToMs(meta.createdAt) !== null) stamp(session, meta.createdAt);
      for (const r of rows) {
        const data = r.data ?? {};
        switch (r.type) {
          case 'user/message': {
            const text = flattenBlocks(data.content).text;
            if (text) session.messages.push({ role: 'user', time: isoToMs(r.time ?? data.time), text });
            break;
          }
          case 'assistant/message': {
            const blocks = flattenBlocks(data.message?.content);
            for (const tool of blocks.tools) {
              session.messages.push({ role: 'tool', time: isoToMs(r.time), tool: tool.name, text: '' });
            }
            if (blocks.text) {
              session.messages.push({ role: 'assistant', time: isoToMs(r.time), text: blocks.text });
            }
            break;
          }
          case 'tool/call':
            session.messages.push({ role: 'tool', time: isoToMs(r.time), tool: data.name ?? 'unknown', text: '' });
            break;
          case 'tool/result': {
            const text = flattenBlocks(data.message?.content).text;
            const last = session.messages[session.messages.length - 1];
            if (last && last.role === 'tool') last.result = text.slice(0, 4000);
            break;
          }
          case 'session/title':
            if (!session.title && typeof data.title === 'string') session.title = data.title;
            break;
          case 'compaction/summary':
            if (typeof data.summary === 'string') {
              session.messages.push({ role: 'system', time: isoToMs(r.time), text: `[compaction] ${data.summary}` });
            }
            break;
          default:
            break;
        }
        stamp(session, r.time ?? data.time);
      }
      if (session.messages.length > 0) sessions.push(session);
    }
  }
  return sessions;
}

// ---------------------------------------------------------------------------
// adapter: codex  (~/.codex)
// ---------------------------------------------------------------------------

const CODEX_ROOT = path.join(HOME, '.codex');

function readCodex() {
  const root = path.join(CODEX_ROOT, 'sessions');
  const sessions = [];
  for (const file of walkFiles(root, (f) => f.endsWith('.jsonl'))) {
    const raw = safeRead(file)?.toString('utf8');
    if (!raw) continue;
    const rows = parseJsonl(raw);
    const meta = rows.find((r) => r.type === 'session_meta')?.payload;
    if (!meta) continue;
    const session = makeSession('codex', String(meta.id ?? path.basename(file, '.jsonl')), file);
    session.cwd = typeof meta.cwd === 'string' ? meta.cwd : null;
    stamp(session, meta.timestamp);
    for (const r of rows) {
      if (r.type === 'event_msg' && r.payload?.type === 'item_completed') {
        const item = r.payload.item ?? {};
        const text = (item.content ?? [])
          .map((c) => (typeof c?.text === 'string' ? c.text : ''))
          .join('\n')
          .trim();
        if (!text) continue;
        const kind = String(item.type ?? '');
        if (/UserMessage/i.test(kind)) session.messages.push({ role: 'user', time: isoToMs(r.timestamp), text });
        else if (/AgentMessage|AssistantMessage/i.test(kind)) {
          session.messages.push({ role: 'assistant', time: isoToMs(r.timestamp), text });
        }
      } else if (r.type === 'response_item' && r.payload?.type === 'message') {
        const role = r.payload.role;
        if (role !== 'user' && role !== 'assistant') continue;
        const text = flattenBlocks(r.payload.content).text;
        if (text) session.messages.push({ role, time: isoToMs(r.timestamp), text });
      }
      stamp(session, r.timestamp);
    }
    if (session.messages.length > 0) sessions.push(session);
  }
  return sessions;
}

// ---------------------------------------------------------------------------
// adapter: VS Code family chat storage
//   VS Code and its forks share one layout, so one adapter covers all of them.
// ---------------------------------------------------------------------------

const VSCODE_PRODUCTS = ['Code', 'Code - Insiders', 'VSCodium', 'Cursor', 'Windsurf', 'Trae'];

function vscodeStorageDirs() {
  const dirs = [];
  for (const root of APP_DATA_ROOTS) {
    for (const product of VSCODE_PRODUCTS) dirs.push(path.join(root, product, 'User', 'workspaceStorage'));
  }
  return dirs;
}

function applyCopilotPatch(target, keys, value) {
  if (!Array.isArray(keys) || keys.length === 0) return;
  let node = target;
  for (let i = 0; i < keys.length - 1; i += 1) {
    const k = keys[i];
    if (node[k] === undefined || typeof node[k] !== 'object') node[k] = typeof keys[i + 1] === 'number' ? [] : {};
    node = node[k];
  }
  node[keys[keys.length - 1]] = value;
}

function readCopilot() {
  const sessions = [];
  // The same storage root can be reachable through more than one app-data convention.
  const seen = new Set();
  for (const storage of vscodeStorageDirs()) {
    if (seen.has(storage)) continue;
    seen.add(storage);
    for (const wsDir of safeReaddir(storage)) {
      if (!wsDir.isDirectory()) continue;
      const wsPath = path.join(storage, wsDir.name);
      const wsJson = safeRead(path.join(wsPath, 'workspace.json'));
      if (!wsJson) continue;
      let folder = null;
      try {
        const raw = JSON.parse(wsJson.toString('utf8')).folder;
        if (typeof raw === 'string') {
          folder = raw.startsWith('file://') ? decodeURIComponent(raw.slice('file://'.length)) : raw;
        }
      } catch {
        continue;
      }
      if (!folder) continue;
      const chatDir = path.join(wsPath, 'chatSessions');
      for (const entry of safeReaddir(chatDir)) {
        if (!entry.isFile() || !entry.name.endsWith('.jsonl')) continue;
        const file = path.join(chatDir, entry.name);
        const text = safeRead(file)?.toString('utf8');
        if (!text) continue;
        const rows = parseJsonl(text);
        let base = null;
        for (const r of rows) {
          if (r.kind === 0 && r.v) base = r.v;
          else if (r.kind === 1 && base) applyCopilotPatch(base, r.k, r.v);
        }
        if (!base) continue;
        const session = makeSession('copilot', entry.name.replace(/\.jsonl$/, ''), file);
        session.cwd = folder;
        if (isoToMs(base.creationDate) !== null) stamp(session, base.creationDate);
        for (const req of base.requests ?? []) {
          const prompt = typeof req.message?.text === 'string' ? req.message.text : '';
          if (prompt) session.messages.push({ role: 'user', time: isoToMs(req.timestamp), text: prompt });
          for (const part of req.response ?? []) {
            const value = typeof part?.value === 'string' ? part.value : '';
            if (value) session.messages.push({ role: 'assistant', time: isoToMs(req.timestamp), text: value });
          }
        }
        if (session.messages.length > 0) sessions.push(session);
      }
    }
  }
  return sessions;
}

// ---------------------------------------------------------------------------
// source registry
//
// To support another tool: write a reader with the signature `() => session[]` and add one entry
// here. `probe()` feeds `doctor`, so keep it to the paths the reader actually opens.
// ---------------------------------------------------------------------------

const SOURCES = [
  {
    id: 'claude-code',
    read: readClaudeCode,
    available: () => exists(path.join(CLAUDE_ROOT, 'projects')),
    probe: () => [path.join(CLAUDE_ROOT, 'projects'), path.join(CLAUDE_ROOT, 'history.jsonl')],
  },
  {
    id: 'dsh',
    read: readDsh,
    available: () => HAS_ZSTD && exists(path.join(DSH_ROOT, 'sessions')),
    probe: () => [
      path.join(DSH_ROOT, 'sessions'),
      path.join(DSH_ROOT, 'storages', 'workspace.json'),
    ],
  },
  {
    id: 'codex',
    read: readCodex,
    available: () => exists(path.join(CODEX_ROOT, 'sessions')),
    probe: () => [path.join(CODEX_ROOT, 'sessions')],
  },
  {
    id: 'copilot',
    read: readCopilot,
    available: () => vscodeStorageDirs().some(exists),
    probe: () => vscodeStorageDirs(),
  },
];

/** Locations of tools this script knows about but does not parse. Reported, never guessed at. */
const UNSUPPORTED_PROBES = [
  ['Gemini CLI', path.join(HOME, '.gemini')],
  ['Continue', path.join(HOME, '.continue')],
  ['OpenCode', path.join(process.env.XDG_DATA_HOME || path.join(HOME, '.local', 'share'), 'opencode')],
  ['Zed', path.join(process.env.XDG_DATA_HOME || path.join(HOME, '.local', 'share'), 'zed')],
  ['Aider (per project)', '.aider.chat.history.md'],
];

// ---------------------------------------------------------------------------
// discovery
// ---------------------------------------------------------------------------

/** Session ids are written with and without a `session-` prefix depending on the producer. */
const bareId = (s) => String(s).replace(/^session-/, '');

/**
 * A session belongs to the project when the *transcript itself* says so.
 * Directory names are a hint only: the slug encoders differ per tool (`:` becomes `-` for Claude
 * but is dropped for DSH), so any slug round-trip is lossy.
 */
function attribute(session, projectNorm, registry) {
  if (within(session.cwd, projectNorm)) return 'cwd';
  const reg = registry.byPath.get(projectNorm);
  if (reg && reg.sessionIds.has(session.id)) return 'registry';
  if (reg) {
    const wanted = new Set([...reg.sessionIds].map(bareId));
    if (wanted.has(bareId(session.id))) return 'registry';
  }
  return null;
}

/**
 * Fallback for transcripts whose recorded cwd is elsewhere but whose *content* names this project.
 * Both separator styles must be probed: transcripts quote one platform's paths while `norm()`
 * produces forward slashes, so a single-form needle silently never matches.
 */
function contentHit(session, projectDir) {
  const abs = absolute(projectDir);
  const needles = [norm(projectDir), abs.replace(/\//g, '\\').toLowerCase()];
  for (const m of session.messages) {
    if (!m.text) continue;
    const t = m.text.toLowerCase();
    if (needles.some((n) => t.includes(n))) return true;
  }
  return false;
}

export function discover(projectDir, options = {}) {
  const projectNorm = norm(projectDir);
  const registry = readDshRegistry();
  const found = [];
  const diagnostics = [];
  for (const source of SOURCES) {
    let all = [];
    try {
      all = source.read();
    } catch (error) {
      diagnostics.push({ source: source.id, reason: String(error?.message ?? error) });
      continue;
    }
    for (const session of all) {
      let match = attribute(session, projectNorm, registry);
      if (!match && options.content && contentHit(session, projectDir)) match = 'content';
      if (match) {
        session.match = match;
        found.push(session);
      }
    }
    diagnostics.push({ source: source.id, scanned: all.length, available: source.available() });
  }
  found.sort((a, b) => (a.startedAt ?? 0) - (b.startedAt ?? 0));
  return {
    project: projectNorm,
    root: absolute(projectDir),
    platform: `${process.platform}/${process.arch}`,
    sessions: found,
    diagnostics,
    registry,
    history: readClaudeHistory(projectNorm),
  };
}

// ---------------------------------------------------------------------------
// reporting
// ---------------------------------------------------------------------------

const pad2 = (n) => String(n).padStart(2, '0');

function fmtTime(ms) {
  if (!ms) return '????-??-?? ??:??';
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function humanBytes(n) {
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)}MB`;
  if (n > 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${n}B`;
}

function shorten(p, max = 46) {
  if (typeof p !== 'string' || p.length <= max) return p ?? '';
  return `…${p.slice(-(max - 1))}`;
}

function sessionRow(s) {
  return {
    source: s.source,
    id: s.id,
    cwd: s.cwd,
    match: s.match,
    title: s.title,
    startedAt: s.startedAt,
    endedAt: s.endedAt,
    messages: s.messages.length,
    userMessages: s.messages.filter((m) => m.role === 'user').length,
    bytes: safeStat(s.file)?.size ?? 0,
    file: s.file,
  };
}

export function renderScan(result) {
  const lines = [];
  lines.push(`project: ${result.root}`);
  lines.push(`env: ${result.platform} · Node ${process.version} · HOME ${HOME}`);
  lines.push('');
  if (result.sessions.length === 0) {
    lines.push('No sessions found for this project.');
    lines.push('If you expected some, re-run with --content to match on the project path inside transcripts.');
  } else {
    lines.push(`Found ${result.sessions.length} session(s):`);
    lines.push('');
    const header = ['source', 'session', 'started', 'msgs', 'size', 'match', 'path'];
    const rows = result.sessions.map((s) => {
      const r = sessionRow(s);
      return [
        r.source,
        r.id.length > 20 ? `${r.id.slice(0, 8)}…${r.id.slice(-6)}` : r.id,
        fmtTime(r.startedAt),
        String(r.messages),
        humanBytes(r.bytes),
        r.match,
        r.file,
      ];
    });
    const widths = header.map((h, i) => Math.max(h.length, ...rows.map((r) => r[i].length)));
    const pad = (cells) => cells.map((c, i) => c.padEnd(widths[i])).join('  ');
    lines.push(pad(header));
    lines.push(widths.map((w) => '─'.repeat(w)).join('  '));
    for (const row of rows) lines.push(pad(row));
  }
  lines.push('');
  for (const d of result.diagnostics) {
    if (d.reason) lines.push(`⚠ ${d.source}: ${d.reason}`);
    else if (d.available === false) lines.push(`· ${d.source}: not installed / no data`);
    else lines.push(`· ${d.source}: scanned ${d.scanned} session(s)`);
  }
  if (result.history.length > 0) lines.push(`· claude history.jsonl: ${result.history.length} prompt(s) for this project`);
  return lines.join('\n');
}

function messageToMarkdown(m, options) {
  if (m.role === 'tool') {
    if (!options.full) return '';
    const result = m.result ? `\n\n\`\`\`\n${m.result}\n\`\`\`` : '';
    return `**🔧 ${m.tool ?? 'tool'}**${result}\n`;
  }
  const who = m.role === 'user' ? '👤 User' : m.role === 'assistant' ? '🤖 Assistant' : '⚙️ System';
  return `### ${who}${m.time ? ` · ${fmtTime(m.time)}` : ''}\n\n${m.text}\n`;
}

export function renderSession(session, options = {}) {
  const head = [
    `# ${session.title ?? session.id}`,
    '',
    `- Source: ${session.source}`,
    `- Session: ${session.id}`,
    `- Working directory: ${session.cwd ?? '(unknown)'}`,
    `- Time: ${fmtTime(session.startedAt)} → ${fmtTime(session.endedAt)}`,
    `- Messages: ${session.messages.length}`,
    `- Original file: ${session.file}`,
    '',
    '---',
    '',
  ].join('\n');
  const body = session.messages.map((m) => messageToMarkdown(m, options)).filter(Boolean).join('\n');
  return `${head}${body}\n`;
}

export function renderIndex(result, files) {
  const lines = [
    `# Agent sessions for this project`,
    '',
    `- Project: \`${result.root}\``,
    `- Environment: ${result.platform} · Node ${process.version}`,
    `- Generated: ${fmtTime(Date.now())}`,
    `- Sessions: ${result.sessions.length}`,
    '',
    '| Source | Session | Time | Messages | Export |',
    '| --- | --- | --- | --- | --- |',
  ];
  result.sessions.forEach((s, i) => {
    lines.push(
      `| ${s.source} | \`${s.id}\` | ${fmtTime(s.startedAt)} → ${fmtTime(s.endedAt)} | ${s.messages.length} | [${path.basename(files[i])}](./${path.basename(files[i])}) |`,
    );
  });
  lines.push('');
  return lines.join('\n');
}

function renderDoctor() {
  const lines = ['Storage probe:', '', `env: ${process.platform} ${process.arch} · Node ${process.version}`, `HOME: ${HOME}`, ''];
  lines.push('Supported sources:');
  for (const source of SOURCES) {
    const paths = source.probe();
    const ok = source.available();
    lines.push(
      `  ${ok ? '✓' : '✗'} ${source.id}${source.id === 'dsh' && !HAS_ZSTD ? '  (needs Node >= 23.8 to decompress)' : ''}`,
    );
    for (const p of paths.slice(0, 4)) {
      const hit = exists(p);
      lines.push(`      ${hit ? '·' : ' '} ${shorten(p, 70)}${hit ? '' : '  (missing)'}`);
    }
    if (paths.length > 4) lines.push(`      … ${paths.length - 4} more candidate location(s)`);
  }
  lines.push('', 'Known but unsupported (presence means sessions this script does not reach):');
  let anyUnsupported = false;
  for (const [label, p] of UNSUPPORTED_PROBES) {
    const hit = exists(p);
    if (hit) anyUnsupported = true;
    lines.push(`  ${hit ? '!' : ' '} ${label.padEnd(20)} ${shorten(p, 60)}`);
  }
  if (!anyUnsupported) lines.push('  (none)');
  lines.push(
    '',
    `zstd: ${HAS_ZSTD ? 'ok' : 'MISSING — the dsh source will be skipped; other sources are unaffected'}`,
    '',
    'Note: unsupported tools never affect the other sources. To add one, write a reader as described at the top of this script and register it in SOURCES.',
  );
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) args[key] = true;
      else {
        args[key] = next;
        i += 1;
      }
    } else args._.push(a);
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0] ?? 'scan';
  const project = args.project ?? process.cwd();

  if (command === 'doctor') {
    process.stdout.write(`${renderDoctor()}\n`);
    return;
  }

  const options = { content: Boolean(args.content) };
  const result = discover(project, options);

  if (command === 'scan') {
    if (args.json) {
      process.stdout.write(
        `${JSON.stringify(
          { ...result, registry: undefined, sessions: result.sessions.map(sessionRow) },
          null,
          2,
        )}\n`,
      );
    } else process.stdout.write(`${renderScan(result)}\n`);
    return;
  }

  if (command === 'export') {
    const outDir = path.resolve(args.out ?? path.join(project, '.agent-sessions'));
    fs.mkdirSync(outDir, { recursive: true });
    const files = [];
    for (const session of result.sessions) {
      const name = `${session.source}--${session.id.replace(/[^a-zA-Z0-9._-]/g, '_')}.md`;
      const target = path.join(outDir, name);
      fs.writeFileSync(target, renderSession(session, { full: Boolean(args.full) }), 'utf8');
      files.push(target);
    }
    if (files.length > 0) fs.writeFileSync(path.join(outDir, 'index.md'), renderIndex(result, files), 'utf8');
    // An export is verbatim: whatever was pasted into a session is pasted into the project.
    // Redacting here, rather than leaving it to whoever remembers, is the difference between a
    // credential in a project directory for a day and one that is never written at all.
    const redaction = redactDirectory(outDir);
    process.stdout.write(`Exported ${files.length} session(s) to ${outDir}\n`);
    for (const f of files) process.stdout.write(`  ${path.basename(f)}\n`);
    const redacted = Object.values(redaction.counts).reduce((a, b) => a + b, 0);
    process.stdout.write(redacted === 0
      ? 'No credential values were found in the export.\n'
      : `Redacted ${redacted} credential value(s) in ${redaction.touched} file(s) — `
        + `key names kept, values replaced.\n`);
    return;
  }

  process.stderr.write(`Unknown command: ${command}\nUsage: scan | export | doctor\n`);
  process.exitCode = 1;
}

main();
