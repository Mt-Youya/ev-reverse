"""One read-only look at what the running player is holding, then exit.

`wait_ready.py` polls in a loop and hunts for a triple; this is the single-shot question "is there
a lesson open, how far has the player decrypted, and is any context still un-keyed". It is the
cheap check before attaching anything heavier, and it leaves nothing attached that could confound a
later instrument.

    python live_contexts.py [--limit 12]
"""

import argparse
import hashlib
import sys
import time

import frida

from wait_ready import JS, key_from_schedule

EXTRA = "20220507"  # the third input, caught live by probe_kdf.py


def derived_key(tk, filename, extra=EXTRA):
    """What `docs/KEY-DERIVATION.md` says the key is: MD5_hex(tk + filename + extra)."""
    return hashlib.md5((tk + filename + extra).encode()).hexdigest()


def pick_pid(explicit=None):
    if explicit:
        return explicit
    cands = [p.pid for p in frida.get_local_device().enumerate_processes()
             if p.name.lower() == 'evplayer2.exe']
    for pid in cands:
        try:
            session = frida.attach(pid)
            probe = session.create_script(
                "send(!!(function(){try{Process.getModuleByName('PlayerLibRender56_vs.dll');"
                "return 1}catch(e){return 0}})());")
            seen = {'v': None}
            probe.on('message', lambda m, d: seen.update(v=m.get('payload')))
            probe.load()
            time.sleep(0.6)
            session.detach()
            if seen['v']:
                return pid
        except Exception:
            continue
    return cands[0] if cands else None


def key_of(context):
    """The key text a context's schedule rebuilds, or None.

    `wait_ready.key_from_schedule` checks the heap-fill pattern but not that what comes out is
    text, so a schedule that is neither fill nor a key can still come back as bytes; every use
    here goes through this, which insists on 32 lowercase hex characters.
    """
    raw = key_from_schedule(context.get('schedule'))
    if not raw:
        return None
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError:
        return None
    return text if len(text) == 32 and all(c in '0123456789abcdef' for c in text) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--pid", type=int,
                    help="attach by pid: the process list can hide an instance this account "
                         "can still attach to, which is what happened the first time this ran")
    args = ap.parse_args()

    pid = pick_pid(args.pid)
    if pid is None:
        print("EVPlayer2.exe is not running.")
        return 1
    print(f"attaching pid={pid} (read-only)")

    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on('message', lambda m, d: None)
    script.load()
    try:
        contexts = script.exports_sync.scan()
    finally:
        session.detach()

    print(f"{len(contexts)} context(s)")
    keyed = [c for c in contexts if key_of(c)]
    print(f"{len(keyed)} hold a key, {len(contexts) - len(keyed)} do not")
    if keyed:
        keys = [key_of(c) for c in keyed]
        print(f"  {len(set(keys))} distinct key(s)")
        for c in keyed[:args.limit]:
            print(f"    {c['file'][:46]:46s} tk={c['token']} key={key_of(c)}")
    empty = [c for c in contexts if not key_of(c)]
    for c in empty[:args.limit]:
        print(f"    (empty) {c['file'][:46]:46s} tk={c['token']}")

    agree = disagree = 0
    for c in keyed:
        if derived_key(c['token'], c['file']) == key_of(c):
            agree += 1
        else:
            disagree += 1
            if disagree <= 3:
                print(f"    MISMATCH {c['file']}\n      schedule {key_of(c)}"
                      f"\n      derived  {derived_key(c['token'], c['file'])}")
    print(f"\nMD5(tk + filename + {EXTRA!r}) == the key the schedule rebuilds: "
          f"{agree}/{agree + disagree}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
