"""Read the lesson's segment list, including `tk`, straight out of the player's memory.

Keys need `tk`, and `tk` comes from the server. Waiting for it means waiting for the player to open a
lesson and talk to the API, and asking for it needs a token that expires daily. But when the player
opens a lesson it has to *hold* the whole list -- every segment's name, signed URL and `tk` -- because
that is what it downloads from. So the list is in memory, and memory does not expire.

`.ts` appears in that memory as the end of a segment name, so one pass for the bytes `.ts\\0` finds
every name the player knows; the `tk` for a name sits within a short distance of it, as 32 hex
characters. This pairs them up, derives the key the project already proved, and then *tests* the key
against the file on disk -- three sync bytes -- so a mis-paired candidate is discarded rather than
believed.

    python tk_scan.py --seconds 60 [--write captured/tk_memory.json]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = r"D:\Downloads\EVPlayer2Downloads"
LIBRARY = os.path.join(HERE, "captured", "keys_merged.json")
NAME = re.compile(rb"[0-9]{4,8}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.ts")
TOKEN = re.compile(rb"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")

JS = r"""
'use strict';
var ranges = Process.enumerateRanges('r--');
var index = 0, found = 0;

function step() {
  var deadline = Date.now() + 500;
  while (index < ranges.length && Date.now() < deadline) {
    var range = ranges[index++];
    if (range.size > 128 * 1024 * 1024) continue;
    var hits;
    try { hits = Memory.scanSync(range.base, range.size, '2e 74 73 00'); } catch (e) { continue; }
    for (var i = 0; i < hits.length; i++) {
      var address = hits[i].address;
      var before = null, after = null;
      // A segment's record is wider than it looks: between a name and its `tk` there is usually the
      // whole signed URL, so a window of a couple of hundred bytes misses it. One kilobyte either
      // side costs nothing and covers a JSON object or a C struct with room to spare.
      try { before = address.sub(1024).readByteArray(1024); } catch (e) {}
      try { after = address.add(3).readByteArray(1024); } catch (e) {}
      if (!before) continue;
      found += 1;
      send({ t: 'hit', before: Array.prototype.map.call(new Uint8Array(before),
             function (b) { return ('0' + b.toString(16)).slice(-2); }).join(' '),
             after: after ? Array.prototype.map.call(new Uint8Array(after),
             function (b) { return ('0' + b.toString(16)).slice(-2); }).join(' ') : '' });
    }
  }
  if (index < ranges.length) setTimeout(step, 0);
  else send({ t: 'done', ranges: ranges.length, hits: found });
}
setTimeout(step, 0);
send({ t: 'armed', ranges: ranges.length });
"""


def renderer_pid():
    listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
    best = None
    for row in listing.splitlines():
        cells = [c.strip('"') for c in row.split('","')]
        if len(cells) >= 5 and cells[0] == "EVPlayer2.exe":
            weight = int(cells[4].replace(",", "").replace(" K", ""))
            if best is None or weight > best[0]:
                best = (weight, int(cells[1]))
    return best[1] if best else None


def opens_with(name, key):
    """The project's own test: three sync bytes after unmasking and decrypting the file's head."""
    try:
        from Crypto.Cipher import AES
    except ImportError:
        return None
    path = os.path.join(CACHE_DIR, name)
    try:
        with open(path, "rb") as handle:
            head = handle.read(576)
    except OSError:
        return None
    if len(head) < 576:
        return None
    mask = hashlib.md5(name.encode()).hexdigest()[:16].encode()
    masked = bytes(b ^ mask[i % 16] for i, b in enumerate(head))
    plain = AES.new(key.encode(), AES.MODE_ECB).decrypt(masked)
    return plain[0] == 0x47 and plain[188] == 0x47 and plain[376] == 0x47


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--write", default=os.path.join(HERE, "captured", "tk_memory.json"))
    args = ap.parse_args()

    pid = args.pid or renderer_pid()
    if not pid:
        print("EVPlayer2 is not running")
        return 1
    print(f"scanning pid {pid}")

    pairs = {}
    names_seen = set()
    done = {}
    script = frida.attach(pid).create_script(JS)

    def on_message(message, data):
        if message.get("type") == "error":
            print("script error:", message.get("description"))
            return
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("t") == "armed":
            print(f"{payload['ranges']} readable range(s) to walk")
        elif payload.get("t") == "done":
            done.update(payload)
        elif payload.get("t") == "hit":
            before = bytes.fromhex(payload["before"])
            after = bytes.fromhex(payload["after"]) if payload["after"] else b""
            window = before + b".ts\x00" + after
            for match in NAME.finditer(window):
                name = match.group().decode()
                names_seen.add(name)
                # The tk for a name sits in the same record; take every 32-hex string in reach and
                # let the decryption test decide which one is right.
                for token in TOKEN.findall(window):
                    pairs.setdefault(name, set()).add(token.decode())

    script.on("message", on_message)
    script.load()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            script.unload()
        except Exception:
            pass

    print(f"{len(names_seen)} distinct segment name(s) seen, "
          f"{len(pairs)} with a candidate tk nearby")
    on_disk = [name for name in names_seen if os.path.exists(os.path.join(CACHE_DIR, name))]
    print(f"{len(on_disk)} of those files are in the download directory")

    library = json.load(open(LIBRARY, encoding="utf-8")) if os.path.exists(LIBRARY) else {}
    already = sum(1 for name in names_seen if name in library)
    print(f"{already} of them were already known")

    proven, tested = {}, 0
    for name, tokens in pairs.items():
        if name in library:
            continue
        for token in tokens:
            tested += 1
            # Two things a 32-hex string next to a filename can be: the `tk` the key is derived from,
            # or the key itself, which is what the player keeps once it has derived it. Testing both
            # costs one AES block and settles it, so no guessing is needed about which the player
            # chose to keep.
            if opens_with(name, token):
                proven[name] = {"key": token, "source": "key in memory"}
                break
            derived = hashlib.md5((token + name + "20220507").encode()).hexdigest()
            if opens_with(name, derived):
                proven[name] = {"key": derived, "tk": token}
                break
    print(f"{tested} candidate(s) tested, {len(proven)} key(s) proven against the file on disk")

    if proven:
        library.update({name: entry["key"] for name, entry in proven.items()})
        with open(LIBRARY, "w", encoding="utf-8") as handle:
            json.dump(library, handle, indent=1, sort_keys=True)
        print(f"library now holds {len(library)} key(s)")
        with open(args.write, "w", encoding="utf-8") as handle:
            json.dump(proven, handle, indent=1, sort_keys=True)
        print(f"wrote {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
