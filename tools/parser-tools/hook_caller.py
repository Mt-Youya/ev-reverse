"""Attribute every AES key installation in EVPlayer2 to its calling code site.

`hook_aes_key.py` caught 2825 distinct 256-bit keys in one session but none of them was a
segment key -- a segment key is a 32-character hex string, so its bytes are all ASCII hex
digits, and not one captured key looked like that. Thousands of keys the player never uses for
video means the AES entry point is shared with something else (string or memory obfuscation),
so the captures need to be told apart by who called them.

This records the caller's return address alongside each key. A per-segment decryptor shows up as
one hot call site emitting one new key per segment; the obfuscation noise shows up as many sites
emitting keys no segment ever accepts. Keys go to an append-only JSONL file so a partial read
can never lose an earlier capture.

Usage:
    python -u hook_caller.py [pid]        # then play a lesson; Ctrl+C to stop
"""

import json
import os
import sys
import time

import frida

RVA_SET_ENCRYPT_KEY = 0x791F90
RVA_SET_DECRYPT_KEY = 0x791D10
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captured", "keys_by_caller.jsonl")

SCRIPT = r"""
const mods = Process.enumerateModules();
let lib = null;
for (const m of mods) {
    if (m.name.toLowerCase() === "playerlibrender56_vs.dll") { lib = m; break; }
}
if (!lib) { send({ t: "error", msg: "PlayerLibRender56_vs.dll not loaded" }); }
else {
    send({ t: "info", msg: "base " + lib.base + " size " + lib.size });
    const sites = [["encrypt", lib.base.add(0x791F90)], ["decrypt", lib.base.add(0x791D10)]];
    for (const [name, addr] of sites) {
        try {
            Interceptor.attach(addr, {
                onEnter(args) {
                    const bits = args[1].toInt32();
                    const n = bits >>> 3;
                    if (n !== 16 && n !== 24 && n !== 32) return;
                    let hexkey, isHexKey = false;
                    try {
                        const b = new Uint8Array(args[0].readByteArray(n));
                        hexkey = Array.from(b).map(v => ("0" + v.toString(16)).slice(-2)).join("");
                        // A segment key reaches AES as the 32 characters of its own hex string.
                        isHexKey = (n === 32) && Array.from(b).every(
                            v => (v >= 0x30 && v <= 0x39) || (v >= 0x61 && v <= 0x66));
                    } catch (e) { return; }
                    const ret = this.returnAddress;
                    let where = "?";
                    try {
                        const m = Process.findModuleByAddress(ret);
                        where = m ? (m.name + "+0x" + ret.sub(m.base).toString(16)) : String(ret);
                    } catch (e) { where = String(ret); }
                    send({ t: "key", site: name, bits: bits, hex: hexkey,
                           hexlike: isHexKey, caller: where, ctx: String(args[2]) });
                }
            });
            send({ t: "info", msg: "hooked " + name + " @ " + addr });
        } catch (e) {
            send({ t: "error", msg: name + ": " + e });
        }
    }
}
"""


def find_pid():
    import subprocess

    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EVPlayer2.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == "evplayer2.exe":
            return int(parts[1])
    return None


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running")
        return 1
    session = frida.attach(pid)
    print("attached to pid %d" % pid)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    handle = open(OUT, "a", encoding="utf-8")
    total = 0
    hexlike = 0
    callers = {}

    def on_message(message, data):
        nonlocal total, hexlike
        if message.get("type") != "send":
            return
        payload = message["payload"]
        kind = payload.get("t")
        if kind == "info":
            print("[hook] %s" % payload["msg"])
        elif kind == "error":
            print("[error] %s" % payload["msg"])
        elif kind == "key":
            total += 1
            record = {
                "site": payload["site"], "bits": payload["bits"], "key": payload["hex"],
                "hexlike": payload["hexlike"], "caller": payload["caller"], "ctx": payload["ctx"],
                "t": time.time(),
            }
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            callers[payload["caller"]] = callers.get(payload["caller"], 0) + 1
            if payload["hexlike"]:
                hexlike += 1
                print("*** HEX-SHAPED KEY  %s  caller=%s  ctx=%s"
                      % (payload["hex"], payload["caller"], payload["ctx"]))

    script = session.create_script(SCRIPT)
    script.on("message", on_message)
    script.load()

    print()
    print("hooked. Play a lesson now; keys are appended to %s" % OUT)
    try:
        while True:
            time.sleep(2)
            if total:
                top = sorted(callers.items(), key=lambda kv: -kv[1])[:5]
                print("  %d keys, %d hex-shaped | top callers: %s"
                      % (total, hexlike, ", ".join("%s x%d" % (c, n) for c, n in top)))
    except KeyboardInterrupt:
        print("\ncaptured %d keys (%d hex-shaped) -> %s" % (total, hexlike, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
