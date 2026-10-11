"""Back ends for purge-uploaded-videos: R2 over SigV4, Bilibili over biliup or HTTP,
media duration, and credential lookup.

Standard library only, on purpose. The skill claims to run on Windows, macOS and Linux,
and a tool that first needs `pip install boto3` does not.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

MIB = 1024 * 1024
# The part size tools/upload_to_r2.py uploads with. An object uploaded with a different
# part size has a different multipart ETag, so the 5 MiB pass below covers the other
# uploader that is common in the wild (rclone's default chunk size).
PART_SIZES = (8 * MIB, 5 * MIB)
BILI_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/126.0.0.0 Safari/537.36")
BILI_ARCHIVES = "https://member.bilibili.com/x/web/archives"
BILI_ARCHIVE_VIEW = "https://member.bilibili.com/x/vupre/web/archive/view"
BILI_REFERER = "https://member.bilibili.com/platform/home"
BILI_EDIT_REFERER = "https://member.bilibili.com/platform/upload-manager/article"
# Bilibili answers 412 (risk control) or -702 (too many requests) when it is asked too
# often. Both mean "no answer", never "nothing there"; docs/BILIBILI-UPLOAD.md records
# the day a verification script read an empty listing as an empty account.
HTTP_RISK = {412, 429, 503}


class BackendError(RuntimeError):
    """A back end could not answer. Never means "the answer is no"."""


def use_utf8_console() -> None:
    """Windows consoles default to a code page that cannot encode the 中文 titles these
    tools print, and a UnicodeEncodeError mid-run would abort a deletion pass."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def norm(title: str) -> str:
    """Lesson name for comparison: no media extension, no whitespace.

    The repeated-extension pattern is deliberate: the platform stores
    `35. [测试体系]单元测试.mp4.mp4` while R2 and the 稿件 part carry
    `35. [测试体系]单元测试.mp4`. Stripping one extension leaves the two unequal and the
    lesson never looks verified, so its scratch is never freed.
    """
    text = re.sub(r"(\.(mp4|mkv|ts|mov|m4v|webm|flv))+$", "", str(title).strip(), flags=re.I)
    return re.sub(r"\s+", "", text)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


# --------------------------------------------------------------------------- credentials


def read_credential_file(path: pathlib.Path) -> dict:
    """Account id, access key and secret from a JSON or AWS-INI credentials file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        blob = json.loads(text)
        flat = {str(k).lower(): v for k, v in blob.items()}
        account = flat.get("account_id") or flat.get("account") or flat.get("r2_account_id")
        access = flat.get("access_key_id") or flat.get("access") or flat.get("aws_access_key_id")
        secret = (flat.get("secret_access_key") or flat.get("secret")
                  or flat.get("aws_secret_access_key"))
        return {"account_id": account, "access_key_id": access, "secret_access_key": secret}

    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip().lower()] = value.strip()
    return {"account_id": found.get("account_id") or found.get("r2_account_id"),
            "access_key_id": found.get("aws_access_key_id"),
            "secret_access_key": found.get("aws_secret_access_key")}


def read_windows_credentials(reader: pathlib.Path, target: str) -> dict:
    """Read R2 keys from Windows Credential Manager through a tiny helper.

    The helper is `build/CredRead.exe` from the ev-reverse repo: one target per call, the
    secret on stdout, DPAPI-encrypted at rest. Nothing is written to disk or printed.
    """

    def one(suffix: str):
        result = subprocess.run([str(reader), f"{target}_{suffix}"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60)
        return result.stdout.strip() if result.returncode == 0 else None

    return {"access_key_id": one("ACCESS_KEY_ID"), "secret_access_key": one("SECRET_ACCESS_KEY")}


def find_credential_reader(explicit: str | None, roots) -> pathlib.Path | None:
    if explicit:
        path = pathlib.Path(os.path.expanduser(explicit))
        return path if path.is_file() else None
    for root in roots:
        for name in ("CredRead.exe", "CredRead"):
            candidate = pathlib.Path(root) / "build" / name
            if candidate.is_file():
                return candidate
    return None


def resolve_credentials(config: dict) -> dict:
    """Account id, access key, secret and where they came from.

    Order: environments, then a credentials file, then the OS keyring helper. The order
    matters: a shell that exports R2_ACCESS_KEY_ID for one command must win over a file
    that was written months ago.
    """
    env = {k: os.environ.get(k) for k in
           ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")}
    file_path = config.get("credentials_file")
    if env["R2_ACCESS_KEY_ID"] and env["R2_SECRET_ACCESS_KEY"]:
        return {"account_id": env["R2_ACCOUNT_ID"] or config.get("account_id"),
                "access_key_id": env["R2_ACCESS_KEY_ID"],
                "secret_access_key": env["R2_SECRET_ACCESS_KEY"], "source": "environment"}

    if file_path:
        path = pathlib.Path(os.path.expanduser(str(file_path)))
        if path.is_file():
            creds = read_credential_file(path)
            if creds["access_key_id"] and creds["secret_access_key"]:
                return {**creds,
                        "account_id": creds["account_id"] or config.get("account_id")
                        or env["R2_ACCOUNT_ID"],
                        "source": f"credentials file {path}"}

    reader = find_credential_reader(config.get("cred_reader"), config.get("repo_dirs") or [])
    if reader and sys.platform == "win32":
        creds = read_windows_credentials(reader, config.get("cred_target", "cf-evmedia-r2"))
        if creds["access_key_id"] and creds["secret_access_key"]:
            return {**creds, "account_id": config.get("account_id") or env["R2_ACCOUNT_ID"],
                    "source": f"Windows Credential Manager via {reader.name}"}

    raise BackendError(
        "no R2 credentials found. Set R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY (plus\n"
        "  R2_ACCOUNT_ID), or point `credentials_file` at a JSON/INI file holding them.\n"
        "  See references/config.md for the file shapes and the keyring recipes.")


# ---------------------------------------------------------------------------- R2 (S3)


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


class R2:
    """The two S3 calls this tool needs, signed with SigV4 over the standard library."""

    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str,
                 timeout: float = 60.0):
        if not account_id:
            raise BackendError("no R2 account id: set R2_ACCOUNT_ID or `r2.account_id` in config")
        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.timeout = timeout

    def _get(self, params: dict) -> bytes:
        query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
                         for k, v in sorted(params.items()))
        uri = "/" + self.bucket
        payload_hash = hashlib.sha256(b"").hexdigest()
        last_error: Exception | None = None
        for attempt in range(1, 5):
            now = datetime.datetime.now(datetime.timezone.utc)
            amzdate = now.strftime("%Y%m%dT%H%M%SZ")
            scope = f"{now.strftime('%Y%m%d')}/auto/s3/aws4_request"
            headers = {"host": self.host, "x-amz-content-sha256": payload_hash,
                       "x-amz-date": amzdate}
            signed = ";".join(sorted(headers))
            canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
            canonical_request = "\n".join(
                ["GET", uri, query, canonical_headers, signed, payload_hash])
            to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                                 hashlib.sha256(canonical_request.encode()).hexdigest()])
            key = _hmac(_hmac(_hmac(_hmac(f"AWS4{self.secret_key}".encode(),
                                          now.strftime("%Y%m%d")), "auto"), "s3"), "aws4_request")
            signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
            request = urllib.request.Request(
                f"https://{self.host}{uri}?{query}",
                headers={**headers, "Authorization":
                         f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
                         f"SignedHeaders={signed}, Signature={signature}"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in HTTP_RISK and error.code < 500:
                    detail = error.read().decode("utf-8", "replace")[:300]
                    raise BackendError(f"R2 answered HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
            time.sleep(2 ** attempt)
        hint = ""
        if "SSL" in str(last_error) or "certificate" in str(last_error).lower():
            hint = ("\n  The TLS handshake to Cloudflare was cut rather than refused: check the "
                    "network, a VPN or a filtering middlebox. Nothing was deleted.")
        raise BackendError(f"R2 listing failed after 4 attempts: {last_error}{hint}")

    def list_objects(self, prefix: str, on_page=None) -> list[tuple[str, int, str]]:
        """Every object under `prefix` as (key, size, etag); folder markers are dropped.

        The listing follows `NextContinuationToken` rather than trusting `IsTruncated`:
        a page that reads as complete while a continuation token is present would silently
        hide every object after the first 1000, and a hidden object looks exactly like one
        that was never uploaded.
        """
        found: list[tuple[str, int, str]] = []
        token = None
        while True:
            params = {"list-type": "2", "max-keys": "1000", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            body = self._get(params)
            root = ET.fromstring(body)
            namespace = root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else ""
            ns = {"s3": namespace} if namespace else {}
            path = "s3:" if namespace else ""

            def text(node, name, default=""):
                found_node = node.find(f"{path}{name}", ns)
                return default if found_node is None or found_node.text is None else found_node.text

            for item in root.findall(f".//{path}Contents", ns):
                key = text(item, "Key")
                try:
                    size = int(text(item, "Size", "0") or 0)
                except ValueError:
                    continue
                etag = text(item, "ETag").strip('"')
                if size > 0 and not key.endswith("/"):
                    found.append((key, size, etag))
            token = text(root, "NextContinuationToken") or None
            if on_page:
                on_page(len(found))
            if not token:
                return found


def file_digests(path: pathlib.Path, chunk: int) -> tuple[str, str]:
    """One pass over the file: its MD5, and the MD5-of-part-MD5s a multipart PUT of
    `chunk`-sized parts would produce. Two passes would double 68 GB of I/O."""
    whole = hashlib.md5()
    parts = []
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            whole.update(block)
            parts.append(hashlib.md5(block).digest())
    joined = hashlib.md5(b"".join(parts)).hexdigest()
    return whole.hexdigest(), f"{joined}-{len(parts)}"


# ------------------------------------------------------------------------- media duration


def ffprobe_path(config: dict) -> str | None:
    explicit = config.get("ffprobe") or os.environ.get("FFPROBE")
    if explicit and pathlib.Path(explicit).exists():
        return str(explicit)
    return shutil.which("ffprobe")


def mp4_duration(path: pathlib.Path) -> float | None:
    """Duration from a QuickTime/MP4 `moov.mvhd`, for machines without ffmpeg."""
    try:
        with path.open("rb") as handle:
            def walk(end: int, wanted: bytes) -> float | None:
                while handle.tell() < end:
                    header = handle.read(8)
                    if len(header) < 8:
                        return None
                    size = int.from_bytes(header[:4], "big")
                    kind = header[4:8]
                    if size == 1:
                        size = int.from_bytes(handle.read(8), "big")
                    elif size == 0:
                        size = end - handle.tell() + 8
                    if size < 8:
                        return None
                    if kind == wanted:
                        return float(size)
                    if kind == b"moov":
                        found = walk(handle.tell() + size - 8, b"mvhd")
                        if found is not None:
                            return found
                    handle.seek(size - 8, os.SEEK_CUR)
                return None

            mvhd_size = walk(path.stat().st_size, b"mvhd")
            if mvhd_size is None:
                return None
            body = handle.read(4)          # version + flags
            version = body[0] if body else 0
            skip = 8 * 2 if version == 1 else 4 * 2
            handle.seek(skip, os.SEEK_CUR)
            timescale = int.from_bytes(handle.read(4), "big")
            duration = int.from_bytes(handle.read(8 if version == 1 else 4), "big")
            return duration / timescale if timescale else None
    except (OSError, ValueError, IndexError):
        return None


def probe_duration(path: pathlib.Path, ffprobe: str | None) -> tuple[float | None, str]:
    """Seconds of media, and how it was read."""
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=120)
            text = (result.stdout or "").strip()
            if text and text.upper() != "N/A":
                return float(text), "ffprobe"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if path.suffix.lower() in (".mp4", ".m4v", ".mov"):
        value = mp4_duration(path)
        if value:
            return value, "mp4 header"
    return None, "unavailable"


# ------------------------------------------------------------------------ bilibili parts


def read_sessdata(config: dict) -> tuple[str | None, str]:
    """SESSDATA and where it came from: an env var, or a cookie file in any of the three
    shapes the ecosystem uses (biliup JSON, a bare cookie list, Netscape cookies.txt)."""
    from_env = config.get("sessdata") or os.environ.get("BILI_SESSDATA")
    if from_env:
        return from_env.strip(), "environment"
    candidates = [config.get("cookies")] if config.get("cookies") else []
    home = pathlib.Path.home()
    candidates += [home / ".biliup" / "cookies.json", home / ".config" / "biliup" / "cookies.json",
                   pathlib.Path("cookies.json")]
    for candidate in candidates:
        if not candidate:
            continue
        path = pathlib.Path(os.path.expanduser(str(candidate)))
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.lstrip().startswith(("{", "[")):
            try:
                blob = json.loads(text)
            except ValueError:
                continue
            cookies = blob
            if isinstance(blob, dict):
                cookies = (blob.get("cookie_info") or {}).get("cookies") or blob.get("cookies") or []
            for cookie in cookies if isinstance(cookies, list) else []:
                if isinstance(cookie, dict) and cookie.get("name") == "SESSDATA":
                    return str(cookie.get("value") or "").strip() or None, str(path)
        else:
            for line in text.splitlines():
                if line.startswith("#") or "\t" not in line:
                    continue
                fields = line.split("\t")
                if len(fields) >= 7 and fields[5] == "SESSDATA":
                    return fields[6].strip(), str(path)
    return None, "not found"


def biliup_path(config: dict) -> str | None:
    explicit = config.get("biliup")
    if explicit:
        path = pathlib.Path(os.path.expanduser(str(explicit)))
        if path.is_file():
            return str(path)
    found = shutil.which("biliup") or shutil.which("biliup.exe")
    if found:
        return found
    for name in ("biliup.exe", "biliup"):
        candidate = pathlib.Path.home() / ".biliup" / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return None


class Bilibili:
    """The account's 稿件 and their parts (分P), from whichever source was asked for.

    `biliup` is preferred when present because it already holds a working session and
    speaks an endpoint that has stayed reachable while the raw member API was answering
    412. The HTTP path exists so the same check works on a machine without the binary.
    """

    def __init__(self, config: dict, cache_dir: pathlib.Path, log=print):
        self.source = config.get("source", "auto")
        self.sessdata, self.cookie_source = read_sessdata(config)
        self.cookie_path = pathlib.Path(self.cookie_source) if self.cookie_source not in (
            "environment", "not found") else pathlib.Path.home() / ".biliup" / "cookies.json"
        self.biliup = biliup_path(config)
        if self.source == "auto":
            self.source = "biliup" if self.biliup else "api"
        if self.source == "biliup" and not self.biliup:
            raise BackendError("bilibili.source is 'biliup' but no biliup binary was found")
        if self.source == "biliup" and not self.cookie_path.is_file():
            # Without this, biliup is invoked, fails, and the retry loop reports "bilibili
            # would not answer for BV..." two minutes later instead of the real cause.
            raise BackendError(
                f"biliup needs a cookie file and {self.cookie_path} does not exist. Run "
                "`biliup login`, or set bilibili.cookies / BILI_SESSDATA and use "
                "bilibili.source = 'api'")
        self.min_interval = float(config.get("min_interval", 0.3))
        self.retries = int(config.get("retries", 3))
        self.cache_file = cache_dir / "bili-parts.json"
        self.cache_minutes = float(config.get("parts_cache_minutes", 720))
        self.log = log
        self._last_call = 0.0
        self.failures: list[tuple[str, str]] = []
        self.fetched_live = 0
        self.from_cache = 0
        # bvid -> "cache" | "live": where the parts used for a decision were read.
        self.provenance: dict[str, str] = {}

    # -- transport ---------------------------------------------------------
    def _pause(self) -> None:
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _http(self, url: str, referer: str) -> dict:
        headers = {"User-Agent": BILI_UA, "Referer": referer, "Cookie": f"SESSDATA={self.sessdata}",
                   "Accept": "application/json, text/plain, */*"}
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    def _call(self, bv: str) -> list[dict]:
        """Parts of one 稿件, retrying through Bilibili's rate refusals."""
        reason = "unknown"
        for attempt in range(1, self.retries + 1):
            self._pause()
            try:
                if self.source == "biliup":
                    result = subprocess.run(
                        [str(self.biliup), "-u", str(self.cookie_path), "show", bv],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=180)
                    brace = (result.stdout or "").find("{")
                    if brace >= 0:
                        return json.loads(result.stdout[brace:]).get("videos") or []
                    reason = (result.stderr or result.stdout or "no output").strip()[:200]
                else:
                    body = self._http(f"{BILI_ARCHIVE_VIEW}?bvid={bv}", BILI_EDIT_REFERER)
                    if body.get("code") == 0:
                        return (body.get("data") or {}).get("videos") or []
                    reason = f"code {body.get('code')}: {body.get('message')}"
            except urllib.error.HTTPError as error:
                reason = f"HTTP {error.code}"
                if error.code == 404:
                    raise BackendError(f"bilibili does not know {bv} (HTTP 404)") from error
            except Exception as error:  # noqa: BLE001 -- any transport failure is retryable
                reason = f"{type(error).__name__}: {error}"
            self.log(f"     ... {bv} refused ({reason}); retry {attempt}/{self.retries}")
            time.sleep(20 * attempt)
        raise BackendError(f"bilibili would not answer for {bv}: {reason}")

    # -- the account -------------------------------------------------------
    def bvids(self) -> list[tuple[str, str]]:
        """[(bvid, 稿件 title)] for the whole account, newest first.

        An account that answers nothing is an error, not an empty account: reading a
        refusal as "no 稿件" is how a collector decides every video still needs uploading.
        """
        if not self.sessdata:
            raise BackendError(
                "no SESSDATA found: set BILI_SESSDATA, or point `bilibili.cookies` at the "
                "cookie file biliup wrote (usually ~/.biliup/cookies.json)")
        rows: list[tuple[str, str]] = []
        page = 1
        while True:
            self._pause()
            url = BILI_ARCHIVES + "?" + urllib.parse.urlencode(
                {"status": "is_pubing,pubed,not_pubed", "pn": page, "ps": 50, "coop": 1,
                 "interactive": 1})
            try:
                body = self._http(url, BILI_REFERER)
            except urllib.error.HTTPError as error:
                raise BackendError(
                    f"bilibili 稿件 list answered HTTP {error.code}"
                    + (" -- that is risk control; wait a few minutes and rerun"
                       if error.code in HTTP_RISK else "")) from error
            if body.get("code") != 0:
                raise BackendError(f"bilibili 稿件 list answered code {body.get('code')}: "
                                   f"{body.get('message')}")
            data = body.get("data") or {}
            audits = data.get("arc_audits") or []
            for row in audits:
                archive = row.get("Archive") or {}
                if archive.get("bvid"):
                    rows.append((archive["bvid"], norm(archive.get("title") or "")))
            total = int((data.get("page") or {}).get("count") or 0)
            if not audits or len(rows) >= total:
                break
            page += 1
        if not rows:
            raise BackendError("bilibili returned an empty 稿件 list -- refusing to treat that "
                               "as 'the account holds nothing'")
        return rows

    # -- parts -------------------------------------------------------------
    def parts_index(self, refresh: bool = False) -> dict:
        """{normalized part title: [(duration, bvid)]} across every 稿件 of the account.

        Entries read from the cache are remembered in `provenance` and must be confirmed
        live (see `confirm`) before anything depends on them: a cached list is evidence of
        what Bilibili held when it was written, and a part edited or removed since would
        otherwise let a local file be deleted that Bilibili no longer carries.
        """
        listing = self.bvids()
        cached = {} if refresh else self._read_cache()
        index: dict[str, list[tuple[float, str]]] = {}
        kept: dict[str, list] = {}
        for bv, title in listing:
            entry = cached.get(bv)
            if entry is not None:
                videos = entry
                self.from_cache += 1
                self.provenance[bv] = "cache"
            else:
                try:
                    videos = self._call(bv)
                except BackendError as error:
                    self.failures.append((bv, str(error)))
                    continue
                self.fetched_live += 1
                self.provenance[bv] = "live"
            kept[bv] = videos
            self._index_into(index, videos, bv)
        if self.fetched_live:
            self._write_cache(kept)
        return index

    def confirm(self, bvids) -> dict:
        """Re-read these 稿件 live; return their parts index and record any refusal.

        This is what makes the cache safe to use: the cached index only *selects* which
        稿件 matter, and every artifact that is about to be deleted is judged again on a
        reading taken in this run.
        """
        index: dict[str, list[tuple[float, str]]] = {}
        updated = self._read_cache()
        for bv in bvids:
            try:
                videos = self._call(bv)
            except BackendError as error:
                self.failures.append((bv, str(error)))
                continue
            self.fetched_live += 1
            self.provenance[bv] = "live"
            updated[bv] = videos
            self._index_into(index, videos, bv)
        if updated:
            self._write_cache(updated)
        return index

    def _index_into(self, index: dict, videos: list, bv: str) -> None:
        for video in videos:
            part = norm(video.get("title") or video.get("part") or "")
            duration = video.get("duration")
            if part and isinstance(duration, (int, float)) and duration:
                index.setdefault(part, []).append((float(duration), bv))

    def _read_cache(self) -> dict:
        if self.cache_minutes <= 0 or not self.cache_file.is_file():
            return {}
        try:
            blob = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        age = (time.time() - float(blob.get("saved_at") or 0)) / 60
        if age > self.cache_minutes:
            return {}
        return {str(k): v for k, v in (blob.get("by_bvid") or {}).items()}

    def _write_cache(self, by_bvid: dict) -> None:
        if self.cache_minutes <= 0:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps({"saved_at": time.time(), "source": self.source, "by_bvid": by_bvid},
                           ensure_ascii=False), encoding="utf-8")
        except OSError as error:
            self.log(f"     (parts cache not written: {error})")

    @staticmethod
    def lookup(index: dict, title: str, duration: float | None, tolerance: float):
        """The 稿件 holding a part with this title and duration, if any."""
        if duration is None:
            return None
        for part_duration, bv in index.get(title, ()):
            if abs(part_duration - duration) <= tolerance:
                return bv, part_duration
        return None
