"""Join every captured key list to every captured API response on filename, then test candidates
for the third input of `MD5hex(tk + filename + extra)`.

The static reading of the key path gives the shape but not the third input; the responses give
`tk` and the signed URL per filename, and the key lists give the key. Where a filename appears in
both, the third input is the only unknown left in one MD5 — so a candidate list can be scored
against real data instead of being argued about.

    python pair_capture.py
"""

import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from key_formula import md5_hex, schedule_to_key  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(ROOT, "tools", "parser-tools")


def key_sources():
    """filename -> key, from every list of keys this repository has captured."""
    out = {}

    path = os.path.join(ROOT, "rust_out", "final", "keys.json")
    if os.path.exists(path):
        data = json.load(open(path, encoding="utf-8"))
        for name, value in data.items():
            if isinstance(value, dict) and "key" in value:
                out.setdefault(name, {})["grab"] = value["key"]
            elif isinstance(value, str):
                out.setdefault(name, {})["grab"] = value

    path = os.path.join(BENCH, "ev2_out", "keys.json")
    if os.path.exists(path):
        for name, key in json.load(open(path, encoding="utf-8")).items():
            out.setdefault(name, {})["ev2_out"] = key

    path = os.path.join(BENCH, "ctx_dump.json")
    if os.path.exists(path):
        for rec in json.load(open(path, encoding="utf-8")):
            key = schedule_to_key(rec["schedule"])
            if key:
                out.setdefault(rec["file"], {})["ctx_dump"] = key

    path = os.path.join(BENCH, "ev2_out", "manifests.capture")
    if os.path.exists(path):
        text = open(path, encoding="utf-8", errors="replace").read()
        for match in re.finditer(r'"file"\s*:\s*"([^"]+)"[^}]*?"key"\s*:\s*"([0-9a-f]{32})"', text):
            out.setdefault(match.group(1), {})["manifest"] = match.group(2)
    return out


def response_sources():
    """filename -> {tk, sf, params, source} from every captured M3U8-list response."""
    out = {}
    paths = sorted(glob.glob(os.path.join(BENCH, "captured", "inflated", "*.json")))
    paths.append(os.path.join(BENCH, "ev2_out", "manifests.capture"))
    for path in paths:
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        for match in re.finditer(r'\{"idx":(\d+),"sf":"([^"]+)","tk":"([0-9a-f]{32})"\}', text):
            idx, sf, tk = match.group(1), match.group(2), match.group(3)
            name = os.path.basename(sf.split("?")[0])
            out[name] = {
                "idx": int(idx), "tk": tk, "sf": sf,
                "params": dict(re.findall(r"[?&]([a-z]+)=([^&]+)", sf)),
                "source": os.path.basename(path),
            }
    return out


def candidates(rec, tk):
    p = rec["params"]
    d_p = ""
    return {
        "md5(tk+file+sign)": md5_hex(tk + rec["_file"] + p.get("sign", "")),
        "md5(tk+file+t)": md5_hex(tk + rec["_file"] + p.get("t", "")),
        "md5(tk+file+t+sign)": md5_hex(tk + rec["_file"] + p.get("t", "") + p.get("sign", "")),
        "md5(tk+file+sid)": md5_hex(tk + rec["_file"] + p.get("sid", "")),
        "md5(tk+file+bid)": md5_hex(tk + rec["_file"] + p.get("bid", "")),
        "md5(tk+file+bid+sid+t)": md5_hex(tk + rec["_file"] + p.get("bid", "") + p.get("sid", "")
                                          + p.get("t", "")),
        "md5(tk+file+v)": md5_hex(tk + rec["_file"] + p.get("v", "")),
        "md5(tk+sf)": md5_hex(tk + rec["sf"]),
        "md5(tk+file+sf)": md5_hex(tk + rec["_file"] + rec["sf"]),
        "md5(tk+file+idx)": md5_hex(tk + rec["_file"] + str(rec["idx"])),
        "md5(tk+d_p+file)": md5_hex(tk + d_p + rec["_file"]),
        "md5(file+tk)": md5_hex(rec["_file"] + tk),
        "md5(tk+file)": md5_hex(tk + rec["_file"]),
    }


def main():
    keys = key_sources()
    responses = response_sources()
    print(f"key lists: {len(keys)} filename(s)")
    print(f"responses: {len(responses)} filename(s)")

    shared = sorted(set(keys) & set(responses))
    print(f"in both: {len(shared)}")
    if not shared:
        return 1

    agree = 0
    for name in shared:
        rec = responses[name]
        rec["_file"] = name
        for origin, key in keys[name].items():
            if candidates(rec, rec["tk"])["md5(tk+file)"] == key:
                agree += 1
    print(f"  md5(tk+file) already matches {agree} of them")

    score = {}
    for name in shared:
        rec = responses[name]
        rec["_file"] = name
        cands = candidates(rec, rec["tk"])
        for origin, key in keys[name].items():
            for label, value in cands.items():
                score.setdefault(label, 0)
                if value == key:
                    score[label] += 1
    print("\ncandidate                       matches")
    for label, count in sorted(score.items(), key=lambda kv: -kv[1]):
        print(f"  {label:<30} {count}/{len(shared)}")

    for name in shared[:2]:
        rec = responses[name]
        print(f"\n  {name}\n    tk   {rec['tk']}\n    sf   {rec['sf']}\n    keys {keys[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
