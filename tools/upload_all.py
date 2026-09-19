"""Uploads every local course that R2 is missing videos from, using tools/r2-upload-rules.json.

One course per rule, so a failure in one does not stop the others, and the summary says exactly which
courses were completed and which were short. Each course goes through the same uploader the
single-course runs used, so the skip decision (MD5 against the bucket's ETag) is identical.

Each course's full output goes to its own log file under build/r2-logs/, and only an ASCII summary is
printed. Printing the child's output straight to the console crashed on this machine: the child writes
UTF-8, the console decodes as GBK, and the replacement characters cannot be re-encoded.

    python upload_all.py [--apply] [--rules <file>]

Without `--apply` it reports what each course would do and uploads nothing.
"""

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
UPLOADER = HERE / "upload_to_r2.py"
DEFAULT_RULES = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-upload-rules.json")
LOG_DIR = pathlib.Path(r"D:\Codes\github\ev-reverse\build\r2-logs")


def parse(output: str) -> dict:
    """Pulls the numbers out of one course's run."""
    facts = {"skipped": None, "planned": None, "uploaded": None, "verified": None}
    for line in output.splitlines():
        if line.startswith("SKIP "):
            facts["skipped"] = int(line.split()[1])
        elif line.startswith("UPLOAD "):
            facts["planned"] = int(line.split()[1])
        elif line.startswith("uploaded ") and "failed" in line:
            facts["uploaded"] = line.strip()
        elif line.startswith("verified "):
            facts["verified"] = line.strip()
    return facts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", type=pathlib.Path, default=DEFAULT_RULES)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    root = pathlib.Path(rules["root"])
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"rules: {len(rules['rules'])} course(s)")
    print(f"mode:  {'UPLOAD' if args.apply else 'dry run (pass --apply to upload)'}\n")

    results = []
    for index, rule in enumerate(rules["rules"], 1):
        local = root / rule["local"]
        prefix = rule["prefix"]
        label = f"[{index}/{len(rules)}]"
        slug = "".join(c if c.isalnum() else "-" for c in rule["local"])[:48].strip("-")
        log = LOG_DIR / f"{index:02d}-{slug}.log"

        if not local.is_dir():
            print(f"{label} MISSING LOCALLY: {local}")
            results.append((rule["local"], prefix, "missing locally", {}))
            continue

        command = [sys.executable, "-u", str(UPLOADER), "--local", str(local), "--prefix", prefix]
        if args.apply:
            command.append("--apply")
        # The child writes UTF-8; this machine's console decodes as GBK. Capture the bytes and decode
        # here instead of letting the child write to the console.
        result = subprocess.run(command, capture_output=True)
        output = (result.stdout or b"").decode("utf-8", "replace") + \
                 (result.stderr or b"").decode("utf-8", "replace")
        log.write_text(output, encoding="utf-8")

        facts = parse(output)
        if "no files under" in output:
            status = "no videos found"
        elif result.returncode != 0:
            status = "FAILED"
        else:
            status = "ok"
        bits = []
        if facts["skipped"] is not None:
            bits.append(f"skip {facts['skipped']}")
        if facts["planned"] is not None:
            bits.append(f"to-upload {facts['planned']}")
        if facts["uploaded"]:
            bits.append(facts["uploaded"])
        if facts["verified"]:
            bits.append(facts["verified"])
        print(f"{label} {status:<10} {rule['local'][:34]:<36} {prefix[:34]:<36} {' | '.join(bits)}")
        print(f"{'':>12} log: {log.name}")
        results.append((rule["local"], prefix, status, facts))

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    for local, prefix, status, facts in results:
        print(f"{local[:40]:<42}{prefix[:40]:<42}{status}")
    failed = [r for r in results if r[2] == "FAILED"]
    print(f"\n{len(results) - len(failed)}/{len(results)} course(s) ok, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
