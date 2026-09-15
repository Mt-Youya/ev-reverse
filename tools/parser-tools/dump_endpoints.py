"""The endpoint table, read out of the running process instead of out of the file.

No `/student/...` literal exists in `PlayerLibRender56_vs.dll`: the paths sit in base64 blobs that
start with `m4OEgjp` and are turned into text at run time. A static scan therefore reports zero
endpoints and no function can be attributed to one. The decrypted text does exist in memory once
the player has started, so this probe reads it there, and then looks for pointers *to* each string
to find who uses it -- which is how a call site with no static referrer gets a name.

    python -u dump_endpoints.py --pid 18340
"""

import argparse
import collections
import json
import os
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "endpoints.json")

SCRIPT = r"""
const dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

function moduleOf(address) {
  try {
    const m = Process.findModuleByAddress(address);
    return m ? { name: m.name, rva: address.sub(m.base).toInt32() } : null;
  } catch (e) {
    return null;
  }
}

// Read a string without trusting the bytes after it: a match near the end of a mapping makes
// `readUtf8String` throw, and dropping those hits is what hid the whole table the first time.
function textAt(address, wide) {
  let bytes;
  try {
    bytes = new Uint8Array(address.readByteArray(200));
  } catch (e) {
    try { bytes = new Uint8Array(address.readByteArray(64)); } catch (e2) { return null; }
  }
  const out = [];
  if (wide) {
    for (let i = 0; i + 1 < bytes.length; i += 2) {
      if (bytes[i] === 0 && bytes[i + 1] === 0) break;
      const code = bytes[i] | (bytes[i + 1] << 8);
      if (code < 32 || code > 0x10ffff) break;
      out.push(String.fromCharCode(code));
    }
  } else {
    for (let i = 0; i < bytes.length; i++) {
      if (bytes[i] === 0) break;
      if (bytes[i] < 32 || bytes[i] > 126) break;
      out.push(String.fromCharCode(bytes[i]));
    }
  }
  return out.length ? out.join('') : null;
}

// Scan every readable range for the two needle families: the decrypted paths, and the encrypted
// blobs they come from. Private (anonymous) ranges matter as much as the module's own: a table
// built at run time lives on the heap.
function scan() {
  const needles = [
    { tag: 'plain', bytes: [0x2f, 0x73, 0x74, 0x75, 0x64, 0x65, 0x6e, 0x74, 0x2f] }, // /student/
    { tag: 'b64', bytes: [0x6d, 0x34, 0x4f, 0x45, 0x67, 0x6a, 0x70], wide: true },     // m4OEgjp, UTF-16
  ];
  const found = [];
  const ranges = Process.enumerateRanges('r--');
  for (const range of ranges) {
    for (const needle of needles) {
      const pattern = needle.bytes.map(b => ('0' + b.toString(16)).slice(-2)).join(' ');
      let matches;
      try {
        matches = Memory.scanSync(range.base, range.size, pattern);
      } catch (e) {
        continue;
      }
      for (const match of matches) {
        const text = textAt(match.address, needle.wide);
        if (!text) continue;
        found.push({ tag: needle.tag, address: match.address.toString(), text: text, where: moduleOf(match.address),
                     range: { base: range.base.toString(), size: range.size, protection: range.protection } });
      }
    }
  }
  return found;
}

// Who points at this string? A vtable slot or a dispatch table entry is the usual answer, and the
// containing function then has a name to hang on the endpoint.
function pointersTo(address) {
  const target = ptr(address);
  const bytes = target.toMatchPattern();
  const hits = [];
  const ranges = Process.enumerateRanges('r--');
  for (const range of ranges) {
    let matches;
    try {
      matches = Memory.scanSync(range.base, range.size, bytes);
    } catch (e) {
      continue;
    }
    for (const match of matches) {
      if (match.address.equals(target)) continue;
      const where = moduleOf(match.address);
      const holder = moduleOf(target);
      hits.push({ at: match.address.toString(), where: where, string: holder });
    }
  }
  return hits;
}

rpc.exports = { scan: scan, pointers: pointersTo };
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    args = ap.parse_args()

    session = frida.attach(args.pid)
    script = session.create_script(SCRIPT)
    errors = []
    script.on("message", lambda message, data: errors.append(message) if message.get("type") == "error" else None)
    script.load()
    print(f"attached to {args.pid}, scanning readable memory ...", flush=True)

    started = time.time()
    found = script.exports_sync.scan()
    print(f"{len(found)} match(es) in {time.time() - started:.1f}s")

    plain = [item for item in found if item["tag"] == "plain"]
    blob = [item for item in found if item["tag"] == "b64"]

    # A `m4OEgjp` blob and its plaintext travel together; report the pairs first, because that is
    # what proves the base64 strings are the endpoint table and not something else.
    print(f"\n--- decrypted paths ({len(plain)}) ---")
    seen = collections.Counter()
    for item in plain:
        text = item["text"].split("\x00")[0]
        where = item["where"]
        seen[text] += 1
        print(f"  {text[:70]:72s} {item['address']:>18s} {where['name'] + '+' + hex(where['rva']) if where else item['range']['protection']}")

    print(f"\n--- encrypted blobs ({len(blob)}) ---")
    for item in blob[:12]:
        print(f"  {item['text'][:60]:62s} {item['address']:>18s}")

    # Only the unique plaintext strings are worth a pointer sweep; each sweep is a full memory pass.
    unique = {}
    for item in plain:
        text = item["text"].split("\x00")[0]
        unique.setdefault(text, item)

    print(f"\n--- who points at each path ({len(unique)} unique) ---")
    report = {}
    for text, item in sorted(unique.items()):
        hits = script.exports_sync.pointers(item["address"])
        report[text] = hits
        places = collections.Counter()
        for hit in hits:
            where = hit["where"]
            places[where["name"] + "+" + hex(where["rva"]) if where else hit["at"]] += 1
        print(f"  {text[:60]:62s} {len(hits):3d} pointer(s): {dict(list(places.items())[:4])}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump({"paths": {text: item for text, item in unique.items()}, "pointers": report},
                  handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {OUT}")
    if errors:
        print(f"{len(errors)} script error(s); first: {errors[0].get('description')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
