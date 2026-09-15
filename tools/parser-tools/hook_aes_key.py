"""Capture the EVPlayer2 API's AES key by hooking OpenSSL's AES_set_encrypt_key.

Static analysis puts OpenSSL's key schedule at RVA 0x791F90 of PlayerLibRender56_vs.dll:
    AES_set_encrypt_key(const unsigned char *userKey, const int bits, AES_KEY *ctx)
so `rcx` points at the raw 16/24/32-byte key and `edx` is the bit count. Hooking there yields
the key itself rather than a guess at where it lives in memory.

AES_set_decrypt_key (RVA 0x791D10) calls the same routine, so a single hook catches every key
the player installs, whichever direction it is used in.

Usage:
    python -u hook_aes_key.py            # hook and wait, printing keys as they are set
The player installs the session key when it talks to the API, so open a lesson or press
download while this is running.
"""

import json
import os
import sys
import time

import frida

DLL = "PlayerLibRender56_vs.dll"
RVA_SET_ENCRYPT_KEY = 0x791F90
RVA_SET_DECRYPT_KEY = 0x791D10
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captured", "aes_keys.json")

SCRIPT = r"""
const modules = Process.enumerateModules();
let target = null;
for (const m of modules) {
    if (m.name.toLowerCase() === "playerlibrender56_vs.dll") { target = m; break; }
}
if (!target) { send({t: "error", msg: "PlayerLibRender56_vs.dll not loaded"}); }
else {
    send({t: "info", msg: "module base " + target.base + " size " + target.size});
    const hooks = [
        ["set_encrypt_key", target.base.add(0x791F90)],
        ["set_decrypt_key", target.base.add(0x791D10)],
    ];
    for (const [name, addr] of hooks) {
        try {
            Interceptor.attach(addr, {
                onEnter(args) {
                    const bits = args[1].toInt32();
                    if (bits !== 0x80 && bits !== 0xc0 && bits !== 0x100) return;
                    const n = bits / 8;
                    try {
                        const bytes = args[0].readByteArray(n);
                        send({t: "key", hook: name, bits: bits, size: n}, bytes);
                    } catch (e) {
                        send({t: "error", msg: name + " read failed: " + e});
                    }
                }
            });
            send({t: "info", msg: "hooked " + name + " at " + addr});
        } catch (e) {
            send({t: "error", msg: "cannot hook " + name + ": " + e});
        }
    }
}
"""


def find_pid():
    import subprocess

    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq EVPlayer2.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == "evplayer2.exe":
            return int(parts[1])
    return None


def main():
    pid = find_pid()
    if pid is None:
        print("EVPlayer2.exe is not running")
        return 1
    session = frida.attach(pid)
    print("attached to pid %d" % pid)

    keys = []

    def on_message(message, data):
        if message.get("type") != "send":
            print("[frida]", message)
            return
        payload = message["payload"]
        if payload.get("t") == "info":
            print("[hook]", payload["msg"])
        elif payload.get("t") == "error":
            print("[error]", payload["msg"])
        elif payload.get("t") == "key":
            entry = {
                "hook": payload["hook"],
                "bits": payload["bits"],
                "key": bytes(data).hex(),
            }
            if entry in keys:
                return
            keys.append(entry)
            print("KEY  %-16s %3d-bit  %s" % (entry["hook"], entry["bits"], entry["key"]))
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            with open(OUT, "w", encoding="utf-8") as handle:
                json.dump(keys, handle, indent=1)

    script = session.create_script(SCRIPT)
    script.on("message", on_message)
    script.load()

    print()
    print("waiting for the player to install an AES key.")
    print("In EVPlayer2: open any lesson, or press download on one. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\ncaptured %d key(s) -> %s" % (len(keys), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
