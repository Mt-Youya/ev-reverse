"""Fetch a lesson's segment list ourselves: build the request, sign it, send it, decrypt the reply.

This is the end of the player's monopoly on the list. Everything the request needs is now known:

* the endpoint — `/student/getPlayTimeKeySignEVS20260515`, which is the list endpoint despite the
  name (`capture_plaintext.py` + `events.jsonl` attribution: 230 of 300 inflations belong to it);
* the body — JSON, AES-128-ECB under `x!@#y.cn_xnk0506`, base64'd into `{"params": ..., "version": 200}`;
* the signature — caught live at `0x1FD60`: `MD5` of the request's fields as a sorted
  `key=value&key=value` string, with `sign` and `type` left out;
* the reply — an envelope whose `result` is AES-128-ECB under the same key, then gzip.

Two things still come from a capture, because they are credentials rather than protocol: the
bearer token, and the lesson's `evs_playkey`. Both are printed by the hooks already in this bench.

    python fetch_list.py --token <bearer> --playkey <key> --liststr "0|0|a.ts,1|0|b.ts"
    python fetch_list.py --from-capture          # reuse the last captured playkey + liststr

Output is a playlist JSON, the same shape `evmedia derive --playlist` consumes.
"""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.request

from Crypto.Cipher import AES

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = b"x!@#y.cn_xnk0506"
HOST = "https://en2v4.ieway.cn"
ENDPOINT = "/student/getPlayTimeKeySignEVS20260515"
CAPTURE_LOG = os.path.join(HERE, "captured", "kdf_inputs_round2.jsonl")

# Everything except `sign` (which is the result) and `type` (which the signer leaves out and the
# request still carries). Order is by key, which is what the player's own preimage showed.
SIGNED_FIELDS = ["app_version", "evs_playkey", "need_zip", "os_name", "platform", "platform_type",
                 "req_time", "ts_liststr"]


def sign(fields):
    canonical = "&".join(f"{name}={fields[name]}" for name in SIGNED_FIELDS)
    return canonical, hashlib.md5(canonical.encode()).hexdigest()


def render(fields):
    """The JSON the player sends: keys in alphabetical order, Qt's three-space indented style.

    Byte-for-byte match is not needed for the server to parse it, but this *is* what it parses, and
    the difference between this and a compact dump is the difference between one experiment and
    two: the server answers `invalid character '\\u0080'` when the padding is wrong, which is a
    decryption failure wearing a parser's clothes.
    """
    lines = ["{"]
    names = sorted(fields)
    for position, name in enumerate(names):
        value = fields[name]
        rendered = f'"{value}"' if isinstance(value, str) else str(value)
        comma = "" if position == len(names) - 1 else ","
        lines.append(f'   "{name}" : {rendered}{comma}')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def payload(fields, version=202):
    """The request body: JSON, AES-128-ECB, PKCS#7, base64, in the envelope the API expects.

    `version` is 202 for this endpoint and is **not decoration**: sent as 200 the server parses the
    params under an older protocol and answers `invalid character '/' looking for beginning of
    value`, which reads like a decryption failure and is a version mismatch. It matches the
    `dkey_ver` the request descriptors carry.
    """
    raw = render(fields).encode("utf-8")
    pad = 16 - (len(raw) % 16)
    padded = raw + bytes([pad]) * pad
    return {"params": base64.b64encode(AES.new(KEY, AES.MODE_ECB).encrypt(padded)).decode(),
            "version": version}


def open_envelope(envelope_bytes):
    """Response envelope -> the JSON inside it, decrypting and inflating as the player would."""
    envelope = json.loads(envelope_bytes.decode("utf-8"))
    result = envelope.get("result")
    if not isinstance(result, str) or len(result) < 16:
        raise SystemExit(f"no result in the envelope: {envelope}")
    plain = AES.new(KEY, AES.MODE_ECB).decrypt(base64.b64decode(result))
    if plain[:2] == b"\x1f\x8b":
        plain = gzip.GzipFile(fileobj=io.BytesIO(plain)).read()
    return envelope, json.loads(plain.decode("utf-8", "strict"))


def from_body(path):
    """The playkey and ts_liststr out of a captured request body, decrypted."""
    envelope = json.load(open(path, encoding="utf-8", errors="replace"))
    blob = base64.b64decode(envelope["params"])
    text = AES.new(KEY, AES.MODE_ECB).decrypt(blob).decode("utf-8", "ignore")
    doc = json.loads(text[text.find("{"):text.rfind("}") + 1])
    return doc["evs_playkey"], doc["ts_liststr"]


def from_capture():
    """The playkey and ts_liststr from the most recent signed request the player built."""
    if not os.path.exists(CAPTURE_LOG):
        raise SystemExit(f"no capture at {CAPTURE_LOG}; run probe_kdf.py while the player fetches")
    best = None
    for line in open(CAPTURE_LOG, encoding="utf-8", errors="replace"):
        try:
            record = json.loads(line)
        except Exception:
            continue
        text = record.get("md5_input", "")
        if text.startswith("app_version="):
            best = text
    if not best:
        raise SystemExit("no signed request in the capture")
    fields = dict(re.findall(r"(?:^|&)([a-z_]+)=([^&]*)", best))
    return fields["evs_playkey"], fields["ts_liststr"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="")
    ap.add_argument("--playkey", default="")
    ap.add_argument("--liststr", default="")
    ap.add_argument("--from-capture", action="store_true",
                    help="take playkey and ts_liststr from the newest captured request")
    ap.add_argument("--from-body", default="",
                    help="take playkey and ts_liststr from a captured request body")
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--out", default=os.path.join(HERE, "captured", "fetched_list.json"))
    ap.add_argument("--dry-run", action="store_true", help="build and print the request only")
    args = ap.parse_args()

    playkey, liststr = args.playkey, args.liststr
    if args.from_body:
        playkey, liststr = from_body(args.from_body)
        print(f"playkey and ts_liststr taken from {os.path.basename(args.from_body)}")
    elif args.from_capture or not (playkey and liststr):
        playkey, liststr = from_capture()
        print("playkey and ts_liststr taken from the last captured request")

    fields = {
        "app_version": "5.0.5",
        "evs_playkey": playkey,
        "need_zip": 1,
        "os_name": "windows",
        "platform": 1,
        "platform_type": 1,
        "req_time": int(time.time()),
        "ts_liststr": liststr,
    }
    canonical, signature = sign(fields)
    fields["sign"] = signature
    fields["type"] = 0
    print(f"segments in ts_liststr: {liststr.count(',') + 1}")
    print(f"canonical length {len(canonical)}, sign {signature}")

    body = render(payload(fields)).encode()
    if args.dry_run:
        print(json.dumps(fields, ensure_ascii=False)[:400])
        print(f"body {len(body)} bytes")
        return 0

    request = urllib.request.Request(
        HOST + args.endpoint, data=body,
        headers={"content-type": "application/json", "accept": "*/*",
                 "user-agent": "restclient-cpp/@restclient-cpp_VERSION@"})
    if args.token:
        request.add_header("authorization", args.token if args.token.startswith("Bearer")
                           else f"Bearer {args.token}")
    else:
        print("warning: no --token; the server is expected to refuse this")

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    print(f"HTTP {response.status}, {len(raw)} bytes")
    envelope, doc = open_envelope(raw)
    print(f"errcode={envelope.get('errcode')} errmsg={envelope.get('errmsg')!r}")

    entries = doc.get("k_l") or []
    print(f"{len(entries)} segment(s) signed; host {doc.get('d_p')}")
    for entry in entries[:3]:
        print(f"   idx {entry.get('idx')}: {entry.get('sf', '')[:96]}")
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
