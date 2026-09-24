"""Check the status page's data against the schema that is supposed to describe it.

courses.json is a deliverable in its own right -- it is what the status page reads and what the
gap reports are derived from -- and it carries a JSON Schema next to it, so "does it still match"
is a question a machine can answer. Two things are checked:

  * the document validates against courses.schema.json (draft 2020-12, via jsonschema);
  * courses.data.js parses to exactly the same document, because the page cannot fetch() a local
    file and reads the script instead -- two encodings of one document can drift.

    python validate_status.py [--out-dir <dir>]
"""

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(r"D:\Codes\github\ev-reverse")
DEFAULT_DIR = REPO / "verify_fresh" / "upload-status"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_DIR)
    args = parser.parse_args()

    document = json.loads((args.out_dir / "courses.json").read_text(encoding="utf-8"))
    schema = json.loads((args.out_dir / "courses.schema.json").read_text(encoding="utf-8"))
    script = (args.out_dir / "courses.data.js").read_text(encoding="utf-8")
    payload = script[script.index("{"):script.rindex("}") + 1]

    problems = []
    try:
        import jsonschema
    except ImportError:
        print("jsonschema is not installed; skipping the schema check", file=sys.stderr)
    else:
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
        for error in errors[:10]:
            problems.append(f"{'/'.join(str(p) for p in error.path) or '<root>'}: {error.message}")
        print(f"schema: {'ok' if not errors else f'{len(errors)} violation(s)'}")

    try:
        same = json.loads(payload) == document
    except json.JSONDecodeError as error:
        same = False
        problems.append(f"courses.data.js is not valid JSON: {error}")
    print(f"courses.data.js matches courses.json: {same}")
    if not same and not problems:
        problems.append("courses.data.js and courses.json differ")

    totals = document["totals"]
    print(f"{document['generated_at']}: {totals['courses']} courses, {totals['episodes']} episodes, "
          f"local={totals['local']} r2={totals['r2']} bilibili={totals['bilibili']} both={totals['both']}")

    for problem in problems:
        print(f"  ! {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
