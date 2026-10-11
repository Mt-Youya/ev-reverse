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
 * Three layers, because no single one of them is enough:
 *   1. **Named fields** — `SESSDATA=abc…`, `bili_jct: abc…`, `"refresh_token": "abc"`, plus a
 *      `Cookie:`/`Set-Cookie:` header and an `Authorization:` header whatever its scheme is
 *      (`Bearer …`, `Basic …`, GitHub's space-separated `token ghp_…`, `AWS4-HMAC-SHA256 …`).
 *   2. **Name shapes** — anything that *ends* in a secret word (`WEBHOOK_SECRET=`, `MY_APP_KEY=`,
 *      `DEPLOY_TOKEN=`) and a password inside a connection string (`postgres://user:pass@host`),
 *      so a project whose names nobody anticipated is still covered. A fixed list alone would only
 *      ever catch the names someone thought of.
 *   3. **Token shapes** — issuer prefixes (`ghp_`, `xoxb-`, `sk-`, `AIza`, `glpat-`, `npm_`, …) and
 *      bare JWTs, which identify themselves with no name in front of them.
 *
 * What is deliberately *out* of layer 1 keeps the artifact usable as evidence: `session` is the
 * export's own front matter (`Session: <id>`, how a reader gets back to the original), `sid` is a
 * parameter of the player's segment URLs, and `auth` is an AWS4 scheme name. A credential behind
 * one of those names inside a real `Cookie:` or `Authorization:` header is still removed.
 */
/**
 * Field and cookie names whose value is a credential. Layer 1 above.
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

/**
 * Tokens whose own shape identifies them, so they are caught even with no name in front of them —
 * a bare `ghp_…` pasted into a shell command is as much a leak as `token=…`. Each prefix is
 * issuer-specific enough that prose does not match it by accident.
 */
const PROVIDER_TOKENS = [
  'gh[pousr]_[A-Za-z0-9]{20,}',                    // GitHub: pat, oauth, user, server, refresh
  'github_pat_[A-Za-z0-9_]{20,}',                  // GitHub fine-grained PAT
  'xox[baprs]-[A-Za-z0-9-]{10,}',                  // Slack
  'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}',      // OpenAI
  '(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}',      // Stripe
  'AIza[A-Za-z0-9_-]{30,}',                        // Google API key
  'glpat-[A-Za-z0-9_-]{16,}',                      // GitLab PAT
  'npm_[A-Za-z0-9]{30,}',
  'SG\\.[A-Za-z0-9_-]{16,}\\.[A-Za-z0-9_-]{16,}',  // SendGrid
  'dop_v1_[a-f0-9]{40,}',                          // DigitalOcean
  'hf_[A-Za-z0-9]{30,}',                           // Hugging Face
  'pypi-[A-Za-z0-9_-]{30,}',
].join('|');

export const RULES = [
  {
    // `SESSDATA=abc`, `bili_jct: abc`, `"refresh_token": "abc"` — the quote is part of the name.
    name: 'name=value pair',
    re: new RegExp(
      String.raw`((?:${SECRET_NAMES})["']?\s*[=:]\s*["']?)(?!\[redacted\])(${VALUE})`, 'gi'),
    to: '$1[redacted]',
  },
  {
    // A fixed list cannot know every project's names, so anything that *ends* in a secret word
    // counts too: WEBHOOK_SECRET=, MY_APP_KEY=, DEPLOY_TOKEN=. One separator is required before
    // the suffix, which is what keeps bare `key:` and `token:` in a code sample out of it.
    name: 'suffixed secret name',
    re: new RegExp(
      String.raw`((?:[A-Za-z0-9]+[_-])+(?:secret|key|token|password|passwd|pwd)["']?\s*[=:]\s*["']?)`
      + String.raw`(?!\[redacted\])(${VALUE})`, 'gi'),
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
  {
    // The whole value of an Authorization header is credential material whatever the scheme is:
    // `Bearer …`, `Basic …`, `token ghp_…` (GitHub, space-separated), `AWS4-HMAC-SHA256 Credential=…`.
    // The two rules below catch the same tokens when the header name is absent.
    name: 'authorization header',
    re: /(^|[\s"'(])((?:proxy-)?authorization\s*:\s*)(?![ \t]*\[redacted\])[^\s"'][^\r\n"']{7,}/gim,
    to: '$1$2[redacted]',
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
  { name: 'provider token', re: new RegExp(String.raw`\b(?:${PROVIDER_TOKENS})`, 'g'), to: '[redacted]' },
  {
    // Three base64url segments, the first of which decodes to `{"` — unambiguous when it stands alone.
    name: 'bare JWT',
    re: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g,
    to: '[redacted]',
  },
  {
    // `postgres://user:pass@host` — the password is the part between the last colon and the `@`.
    name: 'connection string password',
    re: /(\b[a-z][a-z0-9+.\-]{1,20}:\/\/)([^/\s@:]{0,64}):(?!\[redacted\])([^@\s/]{4,})@/gi,
    to: '$1$2:[redacted]@',
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
