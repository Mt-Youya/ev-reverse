"""Every AES key the player sets up, with the code that set it.

`0x20EC0` is the app's own key setup: `(schedule, const char* key, bits, direction)`. Everything
that encrypts or decrypts goes through it — segment keys, response bodies, the descriptor path —
so one hook enumerates the *key material* of every flow at once, and the return address says which
flow it was. That is the question the earlier probes answered one endpoint at a time.

Hook `0x1EA10` alongside it for the plaintext: the key setup says what opened a payload, this says
what the payload was.

The player is not doing one thing at a time: several lessons download at once, playback switches
between them, and a download can be paused half-way. A capture is therefore a stream of
interleaved lessons, and every payload is attributed to the lesson it names (`d_p` plus the
`bid`/`sid` in each signed path) rather than assumed to belong to a single one. The per-lesson
tally printed at the end says which lessons the session actually touched and how much of each was
seen.

    python capture_keys.py --pid 18340 --seconds 900
    python capture_keys.py --follow --seconds 1800

Writes `captured/keys.jsonl` (one line per setup) and, under `captured/api/<stamp>/`, one
`NNN-<caller>.bin` per payload plus `index.jsonl` with the per-payload attribution.
"""

import argparse
import gzip
import io
import json
import os
import re
import subprocess
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "captured", "api")
KEYS = os.path.join(HERE, "captured", "keys.jsonl")

JS = r"""
'use strict';
var dll = Process.getModuleByName('PlayerLibRender56_vs.dll');

function cstring(ptr, max) {
  if (!ptr || ptr.isNull()) return null;
  try {
    var text = ptr.readUtf8String(max);
    return text;
  } catch (e) { return null; }
}

// An MSVC `std::string` is { union { char buf[16]; char* ptr; }, size_t size, size_t capacity }.
// Reading one as a C string returns the bytes of its buffer pointer, which is why every payload
// was reported with `key=None`: the key was right there, one indirection away.
function sstring(ptr) {
  if (!ptr || ptr.isNull()) return null;
  try {
    var size = ptr.add(8).readU64().toNumber();
    var cap = ptr.add(16).readU64().toNumber();
    if (size > 0 && size < 512 && cap >= size) {
      var text = (cap < 16) ? ptr.readUtf8String(size) : ptr.readPointer().readUtf8String(size);
      if (text && text.length === size) return text;
    }
  } catch (e) {}
  return cstring(ptr, 256);
}

// --- key setup: (rcx = schedule, rdx = key text, r8d = bits, r9d = 1 encrypt) ----------------
Interceptor.attach(dll.base.add(0x20EC0), {
  onEnter: function (args) {
    var key = cstring(args[1], 256);
    if (!key || key.length < 4) return;
    send({ t: 'key', key: key, bits: args[2].toInt32(), direction: args[3].toInt32(),
           caller: this.returnAddress.sub(dll.base).toInt32() });
  }
});

// --- response decryption: the plaintext, attributed to the same caller ----------------------
Interceptor.attach(dll.base.add(0x1EA10), {
  onEnter: function (args) {
    this.out = args[0];
    this.caller = this.returnAddress.sub(dll.base).toInt32();
    this.key = sstring(args[2]);
  },
  onLeave: function () {
    try {
      var size = this.out.add(0x10).readU64().toNumber();
      var cap = this.out.add(0x18).readU64().toNumber();
      if (size <= 0 || size > 4 * 1024 * 1024) return;
      var data = (cap < 16) ? this.out.readByteArray(size)
                            : this.out.readPointer().readByteArray(size);
      if (!data) return;
      send({ t: 'payload', caller: this.caller, key: this.key }, data);
    } catch (e) {}
  }
});

send({ t: 'ready', base: dll.base.toString() });
"""


SEGMENT = re.compile(r"bid=(\d+)&sid=(\d+)")


def attribute(blob):
    """Which lesson a payload belongs to, and what shape it is.

    A list names its lesson twice over: `d_p` holds the host with the lesson's uuid, and every
    signed path carries `bid`/`sid`. A descriptor names a `cache_key` instead. Nothing is forced:
    a payload that is neither stays `opaque`, because guessing here would put a wrong lesson in the
    record and the record is the point.
    """
    plain = blob
    if blob[:2] == b"\x1f\x8b":
        try:
            plain = gzip.GzipFile(fileobj=io.BytesIO(blob)).read()
        except Exception:
            return {"kind": "gzip-unreadable"}
    try:
        doc = json.loads(plain.decode("utf-8"))
    except Exception:
        return {"kind": "opaque"}
    if not isinstance(doc, dict):
        return {"kind": "json"}
    if "k_l" in doc:
        segments = []
        for entry in doc.get("k_l", []):
            path = str(entry.get("sf", "")).split("?")[0]
            if path:
                segments.append(path.rsplit("/", 1)[-1])
        host = str(doc.get("d_p", "")).rstrip("/")
        match = SEGMENT.search(json.dumps(doc.get("k_l", [])[:1]))
        return {"kind": "list", "lesson": host.rsplit("/", 1)[-1][:8],
                "bid": match.group(1) if match else None, "sid": match.group(2) if match else None,
                "segments": segments}
    if "cache_key" in doc or "dkey" in doc:
        return {"kind": "descriptor", "lesson": str(doc.get("cache_key", ""))[:8],
                "fields": sorted(doc.keys())}
    return {"kind": "json", "fields": sorted(doc.keys())[:8]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--follow", action="store_true")
    args = ap.parse_args()

    if not args.pid:
        if not args.follow:
            print("pass --pid, or --follow to wait for a fresh player")
            return 1
        args.pid = wait_for_player(set())

    session = frida.attach(args.pid)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(OUT, stamp)
    os.makedirs(out, exist_ok=True)
    index = open(os.path.join(out, "index.jsonl"), "a", encoding="utf-8")

    seen_payload = set()
    seen_key = set()
    payloads = 0
    lessons = {}

    def on_message(message, data):
        nonlocal payloads
        if message.get('type') != 'send':
            print(json.dumps(message)[:200].encode("ascii", "replace").decode(), flush=True)
            return
        payload = message['payload']
        kind = payload.get('t')
        if kind == 'ready':
            print(f"armed at base {payload['base']}", flush=True)
            return
        if kind == 'key':
            signature = (payload['caller'], payload['key'], payload['bits'], payload['direction'])
            if signature in seen_key:
                return
            seen_key.add(signature)
            record = {"at": time.time(), "kind": "key", **payload}
            with open(KEYS, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            direction = "encrypt" if payload['direction'] else "decrypt"
            print(f"KEY  caller={payload['caller']:#08x} {payload['bits']:4d} bit {direction:7s} "
                  f"{payload['key']!r}", flush=True)
            return
        if kind != 'payload' or data is None:
            return
        blob = bytes(data)
        digest = hash(blob)
        if digest in seen_payload:
            return
        seen_payload.add(digest)
        payloads += 1
        where = attribute(blob)
        name = f"{payloads:03d}-{payload['caller']:#08x}.bin"
        with open(os.path.join(out, name), "wb") as handle:
            handle.write(blob)
        index.write(json.dumps({"at": time.time(), "index": payloads, "caller": payload['caller'],
                                "key": payload['key'], "size": len(blob), "file": name, **where},
                               ensure_ascii=False) + "\n")
        index.flush()
        # Every payload counts towards a lesson tally, and a list is what says how many segments
        # that lesson has: the player asks for one list per segment while it downloads, so the
        # union over a session is the closest thing to "how much of this lesson was in play".
        lesson = where.get("lesson")
        tally = None
        if lesson:
            entry = lessons.setdefault(lesson, {"sid": where.get("sid"), "segments": set(),
                                                "payloads": 0, "last": 0})
            entry["payloads"] += 1
            entry["last"] = time.time()
            entry["segments"].update(where.get("segments", []))
            tally = f" lesson={lesson} sid={where.get('sid')} segs={len(entry['segments'])}"
        print(f"[{payloads:03d}] caller={payload['caller']:#08x} {where['kind']:9s} "
              f"key={payload['key']!r} {len(blob):5d} B{tally or ''}", flush=True)

    script = session.create_script(JS)
    script.on('message', on_message)
    script.load()
    print(f"watching {args.seconds:.0f}s — use the player normally", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            session.detach()
        except Exception:
            pass
        index.close()

    print(f"\n{len(seen_key)} distinct key setup(s), {payloads} payload(s)")
    if lessons:
        print(f"{len(lessons)} lesson(s) touched:")
        for lesson, entry in sorted(lessons.items(), key=lambda kv: -kv[1]["payloads"]):
            print(f"  {lesson}  sid={entry['sid']}  {entry['payloads']:4d} payload(s)  "
                  f"{len(entry['segments']):3d} distinct segment(s)")
    return 0


def wait_for_player(known):
    """The pid of an EVPlayer2 that was not running before, waiting until one starts."""
    print("waiting for an EVPlayer2 process…", flush=True)
    while True:
        listing = subprocess.run(["tasklist", "/fi", "imagename eq EVPlayer2.exe", "/fo", "csv"],
                                 capture_output=True, text=True).stdout
        pids = {int(row.split('","')[1]) for row in listing.splitlines()
                if row.startswith('"EVPlayer2.exe"')}
        fresh = pids - known
        if fresh:
            return sorted(fresh)[-1]
        known |= pids
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
