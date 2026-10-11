#!/usr/bin/env node
/**
 * redact-exports.mjs — strip credential values out of exported transcripts.
 *
 * Why this exists: an export is a verbatim copy of a conversation, and conversations contain
 * whatever was pasted into them — session cookies, bearer tokens, API keys, private keys. The
 * exports are written *into the project* (`<project>/.agent-sessions/`), where they get read,
 * linked from guides, copied into issues and eventually committed by a `git add -f` or a
 * removed ignore rule. A transcript is worth keeping; the cookies inside it are not.
 *
 * What it does: replaces the *value* and keeps the key, so the sentence still reads as evidence
 * ("the session shows `SESSDATA=[redacted]`" is the fact worth keeping). Replacements are
 * idempotent — `[redacted]` cannot match any rule, so a second run changes nothing.
 *
 * `find-agent-sessions.mjs export` runs this over what it wrote, so a fresh export is clean by
 * construction. This command exists on its own for exports produced another way, for exports
 * that predate the hook, and for `--check` in a pre-commit hook or CI.
 *
 * Usage (paths relative to this skill directory):
 *   node scripts/redact-exports.mjs [--out <dir>] [--dry-run] [--quiet]
 *   node scripts/redact-exports.mjs --check      # exit 1 when anything would be redacted
 *
 * Default directory: <cwd>/.agent-sessions
 */

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// ---------------------------------------------------------------------------
// rules
// ---------------------------------------------------------------------------

/**
 * Each rule keeps the name it matched (`$1`, `$2`) and rewrites only the value. Values are
 * required to look like secrets — 12+ characters of one unbroken token — so ordinary prose
 * ("the token was empty", "session: a lookup table") is left alone.
 *
 * A cookie pair: `SESSDATA=abc…`, `bili_jct: abc…`, `"token": "abc…"`. The names cover what
 * this ecosystem actually leaks (Bilibili cookies, generic session/auth/api tokens) without
 * trying to be a dictionary.
 */
/**
 * Cookie and field names that carry a credential. Deliberately narrow: a transcript is evidence,
 * and over-redaction destroys the things it is evidence *of*. Three names that look tempting are
 * out on purpose, because in this ecosystem they are identifiers, not secrets:
 *   `session`  — the export's own front matter is `Session: <id>`, which is how a reader gets back
 *                to the original; redacting it breaks the trail the guide cites.
 *   `sid`      — `?bid=119354&sid=1113723` is a parameter of the player's segment URLs.
 *   `auth`     — `AUTH: AWS4-HMAC-SHA256` is a scheme name.
 * A credential hiding behind one of those names in a real `Cookie:` header is still removed, by
 * the cookie-header rule below.
 */
const SECRET_NAMES = [
  'SESSDATA', 'bili_jct', 'DedeUserID__ckMd5', 'sec_ck', 'buvid3',
  'session_?id', 'sessionid', 'token', 'jwt', 'csrf_?token', 'csrftoken',
  'x-?api-?key', 'api_?key', 'apikey', 'access_?token', 'refresh_?token', 'client_?secret',
  'aws_?access_?key_?id', 'aws_?secret_?access_?key', 'secret_?access_?key', 'private_?key',
  'password', 'passwd', 'pwd',
].join('|');

/** Cookie names that are identifiers rather than secrets: shorter, and still not ours to publish. */
const IDENTIFIER_NAMES = 'DedeUserID|buvid3';

/** A value is required to look like a secret: one unbroken token, no spaces, 12+ characters. */
const VALUE = String.raw`[A-Za-z0-9%._~+/=\\-]{12,}`;
/** 6+ for identifiers, which are shorter by nature. */
const IDENTIFIER_VALUE = String.raw`[A-Za-z0-9%._~+/=\\-]{6,}`;

export const RULES = [
  {
    // `SESSDATA=abc`, `bili_jct: abc`, `"refresh_token": "abc"` — the quote is part of the name.
    name: 'name=value pair',
    re: new RegExp(
      String.raw`((?:${SECRET_NAMES})["']?\s*[=:]\s*["']?)(?!\[redacted\])(${VALUE})`, 'gi'),
    to: '$1[redacted]',
  },
  {
    name: 'cookie identifier',
    re: new RegExp(
      String.raw`((?:${IDENTIFIER_NAMES})["']?\s*[=:]\s*["']?)(?!\[redacted\])(${IDENTIFIER_VALUE})`, 'gi'),
    to: '$1[redacted]',
  },
  {
    name: 'JSON cookie object',
    re: new RegExp(
      String.raw`("name"\s*:\s*"(?:${SECRET_NAMES}|${IDENTIFIER_NAMES})"[\s\S]{0,160}?"value"\s*:\s*")`
      + String.raw`(?!\[redacted\])([^"]{6,})`, 'gi'),
    to: '$1[redacted]',
  },
  { name: 'bearer token', re: /(\bBearer\s+)(?!\[redacted\])([A-Za-z0-9\-._~+/]{16,}=*)/gi, to: '$1[redacted]' },
  { name: 'basic auth', re: /(\bBasic\s+)(?!\[redacted\])([A-Za-z0-9+/]{16,}=*)/gi, to: '$1[redacted]' },
  {
    // The value must start with a non-space: without that, `\s*` backtracks over the space and the
    // rule eats its own `[redacted]` on the next run, losing the space and never converging.
    name: 'cookie header',
    re: /(^|[\s"'(])((?:set-)?cookie\s*:\s*)(?![ \t]*\[redacted\])[^\s"'][^\r\n"']{7,}/gim,
    to: '$1$2[redacted]',
  },
  { name: 'aws access key id', re: /\b(AKIA[0-9A-Z]{16})\b/g, to: '[redacted]' },
  {
    name: 'private key block',
    re: /-----BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY-----/g,
    to: '[redacted private key]',
  },
];

/**
 * Transcript-shaped text. Code extensions are included because an export directory collects
 * whatever an agent left beside the transcripts -- this project's own has three `.mjs` helpers --
 * and a hard-coded cookie in one of those is the same leak.
 */
const TEXT_EXTENSIONS = new Set([
  '.md', '.markdown', '.json', '.jsonl', '.txt', '.log', '.csv', '.tsv',
  '.yml', '.yaml', '.toml', '.ini', '.env', '.properties',
  '.mjs', '.js', '.cjs', '.ts', '.ps1', '.sh', '.py',
]);

// ---------------------------------------------------------------------------
// redaction
// ---------------------------------------------------------------------------

/**
 * Redact one string. `counts` accumulates per-rule hits when given.
 * Returns the redacted text; never prints or returns the values it removed.
 */
export function redactText(text, counts = {}) {
  let out = text;
  for (const rule of RULES) {
    out = out.replace(rule.re, (...match) => {
      counts[rule.name] = (counts[rule.name] ?? 0) + 1;
      return rule.to.replace(/\$(\d)/g, (_, n) => match[Number(n)] ?? '');
    });
  }
  return out;
}

function textFiles(dir) {
  const found = [];
  const walk = (current) => {
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      return; // unreadable directory: nothing to protect, and not this tool's failure
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile() && TEXT_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        found.push(full);
      }
    }
  };
  walk(dir);
  return found;
}

/**
 * Redact every text file under `dir`.
 * Returns { files, touched, changed, counts } — `changed` is what actually differed on disk.
 */
export function redactDirectory(dir, { dryRun = false } = {}) {
  const result = { files: 0, touched: 0, changed: 0, unreadable: 0, counts: {}, missing: false };
  if (!fs.existsSync(dir)) {
    result.missing = true;
    return result;
  }
  for (const file of textFiles(dir)) {
    result.files += 1;
    let buffer;
    try {
      buffer = fs.readFileSync(file);
    } catch {
      result.unreadable += 1;
      continue;
    }
    // A transcript can legitimately carry a NUL byte (a session that dumped something binary),
    // and skipping those files silently is how the one with the cookies in it stays unredacted.
    // latin1 is a byte-preserving decoding, so the ASCII rules match around any binary and the
    // untouched bytes are written back exactly as they were.
    const binary = buffer.includes(0);
    const text = buffer.toString(binary ? 'latin1' : 'utf8');
    const counts = {};
    const redacted = redactText(text, counts);
    const hits = Object.values(counts).reduce((a, b) => a + b, 0);
    if (hits === 0) continue;
    result.touched += 1;
    for (const [name, n] of Object.entries(counts)) result.counts[name] = (result.counts[name] ?? 0) + n;
    if (!dryRun) {
      fs.writeFileSync(file, redacted, binary ? 'latin1' : 'utf8');
      result.changed += 1;
    }
  }
  return result;
}

function summary(result, { dryRun, quiet }) {
  const total = Object.values(result.counts).reduce((a, b) => a + b, 0);
  const lines = [];
  if (!quiet) {
    for (const [name, n] of Object.entries(result.counts).sort((a, b) => b[1] - a[1])) {
      lines.push(`  ${name}: ${n}`);
    }
  }
  lines.push(total === 0
    ? `No credential values found in ${result.files} file(s)${dryRun ? ' (dry run)' : ''}.`
    : `${dryRun ? 'Would redact' : 'Redacted'} ${total} value(s) in ${result.touched} of ${result.files} file(s).`);
  // Never report a file as clean because it could not be opened: that is the one failure mode
  // where "nothing found" and "nothing looked at" look identical.
  if (result.unreadable > 0) lines.push(`  ! ${result.unreadable} file(s) could not be read and were not checked.`);
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// cli
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
  const dir = path.resolve(args.out ?? path.join(process.cwd(), '.agent-sessions'));
  if (!fs.existsSync(dir)) {
    process.stderr.write(`No export directory at ${dir}\n`);
    process.exitCode = 1;
    return;
  }
  const dryRun = Boolean(args['dry-run']) || Boolean(args.check);
  const result = redactDirectory(dir, { dryRun });
  process.stdout.write(`${summary(result, { dryRun, quiet: Boolean(args.quiet) })}\n`);
  if (result.touched > 0 && !dryRun) {
    process.stdout.write(`  (values only — key names and context are kept; the originals are `
      + `recoverable by re-running \`export\`)\n`);
  }
  if (args.check && result.touched > 0) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
