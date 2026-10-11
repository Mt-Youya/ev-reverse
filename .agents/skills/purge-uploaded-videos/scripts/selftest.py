#!/usr/bin/env python3
"""Offline self test: run the whole pipeline against two faked platforms.

Both back ends are replaced, so this touches no network and no real video. It exists
because this skill deletes files irreversibly: after editing the scripts, run it to show
that the scan, the three verification phases, the guards and the report still behave.

    python3 selftest.py

Cases, all in one run:

  good.mp4       both proofs, part read from the cache then confirmed live   -> deleted
  stale.mp4      the cache claims a part the live re-read no longer has      -> kept
  nor2.mp4       on Bilibili, no object of that name in the bucket           -> kept
  toctou.mp4     proved, then rewritten before deletion                      -> kept
  ws-good/       complete report, object of the recorded size                -> deleted
  ws-partial/    report status is partial                                    -> kept
  ws-bare/       segments and no report (its .ts must not read as a video)   -> kept
  ws-toctou/     complete report, altered after verification                 -> kept

Exit code 0 when every case behaves, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import backends as bk          # noqa: E402
import purge_uploaded as pu    # noqa: E402

DURATION = 5.0
CACHE_GOOD, CACHE_STALE, LIVE = "BVCACHEGOOD", "BVCACHESTALE", "BVLIVE"


class FakeR2:
    """A bucket holding whatever the test put in `objects`."""

    objects: list = []

    def __init__(self, *args, **kwargs):
        pass

    def list_objects(self, prefix, on_page=None):
        return list(self.objects)


class FakeBilibili:
    """An account whose part lists come from the cache, with one part edited away since."""

    lookup = staticmethod(bk.Bilibili.lookup)
    titles: dict = {}

    def __init__(self, config, cache_dir, log=print):
        self.source, self.biliup = "fixture", None
        self.cookie_source = "fixture"
        self.cache_file = cache_dir / "bili-parts.json"
        self.failures, self.fetched_live, self.from_cache = [], 0, 0
        self.provenance = {CACHE_GOOD: "cache", CACHE_STALE: "cache", LIVE: "live"}
        self.parts: dict = {}
        for title, (seconds, bv) in self.titles.items():
            self.parts.setdefault(title, []).append((seconds, bv))

    def parts_index(self, refresh=False):
        return self.parts

    def confirm(self, bvids):
        assert set(bvids) == {CACHE_GOOD, CACHE_STALE}, f"unexpected confirm set: {bvids}"
        live: dict = {}
        for title, entries in self.parts.items():
            for seconds, bv in entries:
                if bv == CACHE_GOOD:            # CACHE_STALE's part is gone from the live read
                    live.setdefault(title, []).append((seconds, bv))
        return live


def main() -> int:
    fixture = pathlib.Path(tempfile.mkdtemp(prefix="purge-selftest-"))
    out, work = fixture / "out", fixture / "work"
    out.mkdir(parents=True)
    for name in ("ws-good", "ws-partial", "ws-bare", "ws-toctou"):
        (work / name).mkdir(parents=True)

    for name in ("good.mp4", "stale.mp4", "nor2.mp4", "toctou.mp4"):
        (out / name).write_bytes(b"selftest" * 512)

    def report_for(directory, published, status="complete"):
        (directory / "list.json").write_text("{}", encoding="utf-8")
        (directory / "manifest.json").write_text("{}", encoding="utf-8")
        (directory / "lesson.ts").write_bytes(b"x" * 8192)
        (directory / "report.json").write_text(json.dumps({
            "tool": "fixture", "status": status, "output": str(published),
            "bytes": (out / "good.mp4").stat().st_size, "output_seconds": DURATION,
            "segments": 2,
        }, ensure_ascii=False), encoding="utf-8")

    # The published paths these workspaces name are already gone, which is the common case:
    # only the report's own title, size and duration are left to prove anything with.
    report_for(work / "ws-good", out / "ws-good-published.mp4")
    report_for(work / "ws-partial", out / "ws-good-published.mp4", status="partial")
    report_for(work / "ws-toctou", out / "ws-toctou-published.mp4")
    (work / "ws-bare" / "list.json").write_text("{}", encoding="utf-8")
    (work / "ws-bare" / "119354-dead-beef.ts").write_bytes(b"x" * 8192)

    clip_size = (out / "good.mp4").stat().st_size

    def md5(name):
        return hashlib.md5((out / name).read_bytes()).hexdigest()

    FakeR2.objects = [(f"videos/selftest/{name}", clip_size, md5(name))
                      for name in ("good.mp4", "stale.mp4", "toctou.mp4")]
    FakeR2.objects += [("videos/selftest/ws-good-published.mp4", clip_size, "0" * 32),
                       ("videos/selftest/ws-toctou-published.mp4", clip_size, "0" * 32)]
    # nor2.mp4 deliberately has no object.

    FakeBilibili.titles = {bk.norm(name): (DURATION, bv) for name, bv in
                           (("good.mp4", CACHE_GOOD), ("stale.mp4", CACHE_STALE),
                            ("nor2.mp4", LIVE), ("toctou.mp4", LIVE),
                            ("ws-good-published.mp4", LIVE),
                            ("ws-toctou-published.mp4", LIVE))}

    bk.resolve_credentials = lambda config: {"account_id": "fixture", "access_key_id": "x",
                                            "secret_access_key": "y", "source": "selftest"}
    bk.probe_duration = lambda path, ffprobe: (DURATION, "selftest")
    bk.R2, bk.Bilibili = FakeR2, FakeBilibili

    # Rewrite two artifacts between verification and deletion: the pre-delete re-check has
    # to notice and leave them alone. Hashes.save() runs at exactly that point.
    real_save = pu.Hashes.save

    def save_then_alter(self):
        real_save(self)
        for victim in (out / "toctou.mp4", work / "ws-toctou" / "lesson.ts"):
            if victim.exists():
                with victim.open("ab") as handle:
                    handle.write(b"changed after verification")

    pu.Hashes.save = save_then_alter

    (fixture / "config.json").write_text(json.dumps({
        "roots": [str(out), str(work)],
        "cache_dir": str(fixture / "cache"),
        "r2": {"bucket": "selftest-bucket", "prefix": "videos/", "account_id": "fixture"},
        "bilibili": {"source": "auto"},
        "min_age_minutes": 0,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    sys.argv = ["purge_uploaded.py", "--config", str(fixture / "config.json"), "--apply"]
    code = pu.main()

    report = json.loads((fixture / "cache" / "last-report.json").read_text(encoding="utf-8"))
    by_name = {pathlib.Path(a["path"]).name: a for a in report["artifacts"]}
    expected = {"good.mp4": "deleted", "stale.mp4": "keep", "nor2.mp4": "keep",
                "toctou.mp4": "keep", "ws-good": "deleted", "ws-partial": "keep",
                "ws-bare": "keep", "ws-toctou": "keep"}
    bad = []
    print("\nselftest cases")
    for name, want in expected.items():
        item = by_name.get(name, {})
        on_disk = (out / name).exists() or (work / name).exists()
        ok = (item.get("decision") == want
              and on_disk == (want == "keep"))
        print(f"  {'ok ' if ok else 'BAD'}  {name:<14} {item.get('decision', '?'):<8} "
              f"on-disk={str(on_disk):<5} {item.get('reason', '')[:56]}")
        if not ok:
            bad.append(name)

    checks = [
        (report["deleted"]["count"] == 2, f"deleted count {report['deleted']['count']} != 2"),
        (len(report["deleted"]["changed_since_verified"]) == 2,
         "the two rewritten artifacts were not reported"),
        (by_name["stale.mp4"]["reason"].startswith("B站 no longer holds"),
         "a stale cached part was not caught by the live re-read"),
        (not report["deleted"]["failed"], f"deletions failed: {report['deleted']['failed']}"),
    ]
    for ok, message in checks:
        if not ok:
            bad.append(message)

    shutil.rmtree(fixture, ignore_errors=True)
    print("\n" + ("selftest: all cases pass" if not bad else f"selftest FAILED: {bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
