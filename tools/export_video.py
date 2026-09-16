"""播放一节 EVPlayer2 视频，自动下载、解密整节课并导出 MP4/MKV。"""

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "tools/parser-tools/captured/runtime"
if RUNTIME.exists():
    sys.path.insert(0, str(RUNTIME))

NAME = re.compile(r"\d+-[0-9a-fA-F-]{36}\.ts")
SUPPORTED_DLL = "8bd3e21c14ad3c760b0c2400ce38d8be3afbdaf9b347af6a40f05c1788229635"
HOOKS = r"""
rpc.exports = {
modulepath() { return Process.getModuleByName('PlayerLibRender56_vs.dll').path; },
arm() {
const dll = Process.getModuleByName('PlayerLibRender56_vs.dll');
Interceptor.attach(dll.base.add(0x1FD60), {
  onEnter(args) {
    try {
      const text = args[1].readUtf8String();
      if (text && text.startsWith('app_version=') && text.includes('evs_playkey='))
        send({t: 'kdf', input: text, at: Date.now() / 1000});
    } catch (_) {}
  }
});
const h2 = Process.getModuleByName('nghttp2.dll');
const seen = new Set();
for (const name of ['nghttp2_submit_request', 'nghttp2_submit_request2']) {
  const address = h2.findExportByName(name);
  if (!address || seen.has(address.toString())) continue;
  seen.add(address.toString());
  Interceptor.attach(address, {
    onEnter(args) {
      try {
        const headers = {};
        const count = args[3].toInt32();
        if (count < 0 || count > 64) return;
        for (let i = 0; i < count; i++) {
          const nv = args[2].add(i * 40);
          const nl = nv.add(16).readU64().toNumber();
          const vl = nv.add(24).readU64().toNumber();
          if (nl <= 0 || nl > 256 || vl > 8192) continue;
          const key = nv.readPointer().readUtf8String(nl);
          if (key === 'authorization' || key === ':path' || key === ':authority')
            headers[key] = nv.add(8).readPointer().readUtf8String(vl);
        }
        if (headers.authorization) send({t: 'request', headers, at: Date.now() / 1000});
      } catch (_) {}
    }
  });
}
send({t: 'armed'});
}
};
"""


def parse_vod(text):
    """Only an ended media playlist establishes the complete segment set and its order."""
    lines = text.strip().splitlines()
    if not lines or lines[0] != "#EXTM3U" or "#EXT-X-ENDLIST" not in lines:
        raise ValueError("不是带结束标记的完整播放清单")
    names, durations = [], []
    duration = None
    for line in lines[1:]:
        line = line.strip()
        if line == "#EXT-X-ENDLIST":
            break
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:") and line != "#EXT-X-MEDIA-SEQUENCE:0":
            raise ValueError("播放清单不是从第一段开始")
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("分段时长无效")
        elif line and not line.startswith("#"):
            name = line.split("?", 1)[0].rsplit("/", 1)[-1]
            if duration is None or not NAME.fullmatch(name):
                raise ValueError("播放清单含不支持的分段")
            names.append(name)
            durations.append(duration)
            duration = None
    if duration is not None or not names or len(set(names)) != len(names):
        raise ValueError("播放清单缺段或重复")
    return {"names": names, "durations": durations, "seconds": sum(durations), "text": text}


def request_fields(preimage):
    fields = dict(part.split("=", 1) for part in preimage.split("&") if "=" in part)
    playkey = fields.get("evs_playkey", "")
    requested = fields.get("ts_liststr", "")
    names = [part.split("|")[-1] for part in requested.split(",")]
    if not playkey or not names or any(not NAME.fullmatch(name) for name in names):
        raise ValueError("尚未取得当前视频的播放凭据")
    return playkey, names


def select_playlist(playlists, requested):
    matches = [p for p in playlists if set(requested).issubset(p["names"])]
    unique = {tuple(p["names"]): p for p in matches}
    if len(unique) != 1:
        raise ValueError("尚未找到唯一匹配当前视频的完整清单")
    return next(iter(unique.values()))


def saved_preimages(vod, work):
    paths = list((work / "sessions").glob("*.jsonl"))
    paths += list((ROOT / "tools/parser-tools/captured/local_decode").glob("*/events.jsonl"))
    found = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                text = event.get("input", "")
                if event.get("t") == "kdf" and "evs_playkey=" in text:
                    if set(request_fields(text)[1]).issubset(vod["names"]):
                        found.append((event.get("at", 0), text))
            except (ValueError, KeyError):
                continue
    return [text for _, text in sorted(found, reverse=True)]


class ProcessMemory:
    def __init__(self, pid):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        class MBI(ctypes.Structure):
            _fields_ = [("base", ctypes.c_void_p), ("allocation", ctypes.c_void_p),
                        ("allocation_protect", wt.DWORD), ("partition", wt.WORD),
                        ("size", ctypes.c_size_t), ("state", wt.DWORD),
                        ("protect", wt.DWORD), ("kind", wt.DWORD)]
        self.mbi = MBI
        self.api.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.api.OpenProcess.restype = ctypes.c_void_p
        self.api.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
        self.api.VirtualQueryEx.restype = ctypes.c_size_t
        self.api.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.api.OpenProcess(0x410, False, pid)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "无法读取播放器进程")

    def close(self):
        self.api.CloseHandle(self.handle)

    def read(self, address, size):
        data = ctypes.create_string_buffer(size)
        actual = ctypes.c_size_t()
        if self.api.ReadProcessMemory(self.handle, address, data, size, ctypes.byref(actual)):
            return data.raw[:actual.value]
        return b""

    def snapshot(self):
        address, found = 0, {}
        preimages, tokens = set(), set()
        while True:
            info = self.mbi()
            if not self.api.VirtualQueryEx(self.handle, address, ctypes.byref(info), ctypes.sizeof(info)):
                break
            base = info.base or 0
            end = base + info.size
            if end <= address:
                break
            if info.state == 0x1000 and info.kind != 0x1000000 and not info.protect & 0x101:
                cursor = base
                while cursor < end:
                    # Overlap includes playlists spanning a read boundary.
                    raw = self.read(cursor, min(8 * 1024 * 1024, end - cursor))
                    for match in re.finditer(rb"app_version=5\.0\.5&evs_playkey=", raw):
                        start = match.start()
                        stop = raw.find(b"&&ieway.cn@20200611", start)
                        if 0 <= stop - start < 128000 and b"\0" not in raw[start:stop]:
                            try:
                                preimage = raw[start:stop].decode("utf-8")
                                request_fields(preimage)
                                preimages.add(preimage)
                            except (ValueError, UnicodeError):
                                continue
                    tokens.update(token.decode() for token in re.findall(
                        rb"Bearer (eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", raw))
                    for match in re.finditer(b"#EXTM3U\r?\n", raw):
                        start = match.start()
                        stop = raw.find(b"#EXT-X-ENDLIST", start)
                        if stop < 0 or b"\0" in raw[start:stop]:
                            continue
                        try:
                            vod = parse_vod(raw[start:stop + 14].decode("utf-8"))
                            found[tuple(vod["names"])] = vod
                        except (ValueError, UnicodeError):
                            continue
                    cursor += 4 * 1024 * 1024
            address = end
        return list(found.values()), list(preimages), list(tokens)

    def playlists(self):
        return self.snapshot()[0]


def run(command, stage, log):
    print(stage, flush=True)
    with log.open("ab") as output:
        done = subprocess.run([str(v) for v in command], stdout=output, stderr=output)
    if done.returncode:
        raise RuntimeError(f"{stage}失败，详见 {log}")


def fetch_full(vod, playkey, token, args, work, log):
    complete = {"d_p": "", "k_l": []}
    for offset in range(0, len(vod["names"]), 100):
        names = vod["names"][offset:offset + 100]
        source, target = work / "request.jsonl", work / "batch.json"
        liststr = ",".join(f"{i}|0|{name}" for i, name in enumerate(names))
        source.write_text(json.dumps({"md5_input": f"app_version=5.0.5&evs_playkey={playkey}&ts_liststr={liststr}"}) + "\n")
        run([args.cli, "fetch", "--from-capture", source, "--token", token, "--output", target],
            f"取得分段密钥 {offset + 1}–{offset + len(names)}/{len(vod['names'])}", log)
        document = json.loads(target.read_text(encoding="utf-8"))
        entries = sorted(document["k_l"], key=lambda e: e["idx"])
        actual = [e["sf"].split("?", 1)[0].rsplit("/", 1)[-1] for e in entries]
        if actual != names or [e["idx"] for e in entries] != list(range(len(names))):
            raise RuntimeError("服务器返回的分段不等于原始清单，拒绝合成")
        if not complete["d_p"]:
            complete["d_p"] = document["d_p"]
        for entry in entries:
            entry["idx"] += offset
            if document["d_p"] != complete["d_p"] and not entry["sf"].startswith("http"):
                entry["sf"] = document["d_p"].rstrip("/") + entry["sf"]
        complete["k_l"].extend(entries)
    playlist = work / "list.json"
    playlist.write_text(json.dumps(complete), encoding="utf-8")
    return complete


def publish(partial, output):
    for attempt in range(121):
        try:
            partial.replace(output)
            return
        except OSError as error:
            if getattr(error, "winerror", None) != 32 or attempt == 120:
                raise
            time.sleep(.25)


def export(vod, preimage, token, args):
    playkey, requested = request_fields(preimage)
    select_playlist([vod], requested)
    identity = hashlib.sha256("\n".join(vod["names"]).encode()).hexdigest()[:16]
    work = args.work / identity
    work.mkdir(parents=True, exist_ok=True)
    (work / "original.m3u8").write_text(vod["text"], encoding="utf-8")
    playlist, enc, manifest, merged = work / "list.json", work / "enc", work / "manifest.json", work / "lesson.ts"
    log = work / "export.log"
    document = fetch_full(vod, playkey, token, args, work, log)
    entries = sorted(document["k_l"], key=lambda e: e["idx"])
    actual = [e["sf"].split("?", 1)[0].rsplit("/", 1)[-1] for e in entries]
    if actual != vod["names"] or [e["idx"] for e in entries] != list(range(len(actual))):
        raise RuntimeError("服务器返回的分段不等于原始整课清单，拒绝合成")
    enc.mkdir(exist_ok=True)
    if args.cache and args.cache.is_dir():
        for name in vod["names"]:
            cached, target = args.cache / name, enc / name
            if cached.exists() and cached.stat().st_size >= 376 and cached.stat().st_size % 16 == 0 and not target.exists():
                shutil.copyfile(cached, target)
    run([args.cli, "download", playlist, enc, "--parallel", args.jobs],
        "下载整课分段（已有分段复用）", log)
    run([args.cli, "derive", "--playlist", playlist, "--input", enc, "--output", manifest],
        "推导并校验全部分段密钥", log)
    if not merged.exists():
        run([args.cli, "decode-ev", enc, manifest, merged], "解密并按原始清单顺序合并", log)
    output = args.output if args.output else args.directory / f"{identity}.{args.container}"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() not in (".mp4", ".mkv"):
        raise ValueError("输出文件扩展名必须为 .mp4 或 .mkv")
    partial = output.with_name(output.stem + ".partial" + output.suffix)
    command = [args.ffmpeg, "-v", "warning", "-nostdin", "-y", "-i", merged,
               "-map", "0:v:0", "-map", "0:a?", "-c", "copy"]
    if output.suffix.lower() == ".mp4":
        command += ["-movflags", "+faststart"]
    run(command + [partial], "合成视频和音频", log)
    probe = subprocess.run([args.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(partial)],
                           capture_output=True, text=True, encoding="utf-8", check=True)
    info = json.loads(probe.stdout)
    seconds = float(info["format"]["duration"])
    if abs(seconds - vod["seconds"]) > max(2, vod["seconds"] * .002):
        raise RuntimeError(f"输出时长 {seconds:.3f}s 与完整清单 {vod['seconds']:.3f}s 不一致")
    if not any(s["codec_type"] == "video" for s in info["streams"]):
        raise RuntimeError("合成结果缺少视频轨道")
    run([args.ffmpeg, "-v", "error", "-xerror", "-threads", "4", "-i", partial,
         "-progress", work / "validation.progress", "-nostats", "-f", "null", "-"],
        "完整解码校验视频和音频", log)
    report = {"status": "complete", "segments": len(actual), "playlist_seconds": vod["seconds"],
              "output_seconds": seconds, "output": str(output.resolve()), "bytes": partial.stat().st_size,
              "streams": [{k: s[k] for k in ("codec_type", "codec_name")} for s in info["streams"]]}
    publish(partial, output)
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"整课导出完成：{output.resolve()}（{seconds:.1f} 秒，{len(actual)} 个分段）", flush=True)
    return output


def from_capture(args):
    events = [json.loads(line) for line in (args.from_capture / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    preimage = next(e["input"] for e in reversed(events) if e.get("t") == "kdf" and "evs_playkey=" in e.get("input", ""))
    token = next(e["headers"]["authorization"] for e in reversed(events) if e.get("t") == "request" and "authorization" in e.get("headers", {}))
    _, requested = request_fields(preimage)
    if args.playlist:
        vod = select_playlist([parse_vod(args.playlist.read_text(encoding="utf-8"))], requested)
    else:
        memory = ProcessMemory(args.pid)
        try:
            vod = select_playlist(memory.playlists(), requested)
        finally:
            memory.close()
    export(vod, preimage, token, args)


def watch(args):
    import frida
    device = frida.get_local_device()
    completed = set()
    print("等待 EVPlayer2 播放视频；无需播放到结尾。Ctrl+C 停止监听。", flush=True)
    while True:
        pids = [args.pid] if args.pid else [p.pid for p in device.enumerate_processes() if p.name.lower() == "evplayer2.exe"]
        if not pids:
            time.sleep(2)
            continue
        # The renderer is normally the instance with the largest resident memory.
        def memory_size(pid):
            try:
                api = ctypes.WinDLL("psapi")
                memory = ProcessMemory(pid)
                values = (ctypes.c_size_t * 10)()
                values[0] = ctypes.sizeof(values)
                api.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
                api.GetProcessMemoryInfo(memory.handle, values, ctypes.sizeof(values))
                memory.close()
                return values[2]
            except OSError:
                return 0
        pid = max(pids, key=memory_size)
        inbox = queue.Queue()
        try:
            session = device.attach(pid)
        except frida.ProcessNotFoundError:
            time.sleep(2)
            continue
        memory = ProcessMemory(pid)
        script = session.create_script(HOOKS)
        session_dir = args.work / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        event_path = session_dir / f"{time.time_ns()}-{pid}.jsonl"
        def on_message(message, data):
            if message.get("type") == "send":
                event = message["payload"]
                if event["t"] in ("kdf", "request"):
                    with event_path.open("a", encoding="utf-8") as log:
                        log.write(json.dumps(event) + "\n")
                inbox.put(event)
            else:
                print("播放器探针错误：", message.get("description", message), flush=True)
        script.on("message", on_message)
        session.on("detached", lambda *unused: inbox.put({"t": "detached"}))
        script.load()
        dll_path = Path(script.exports_sync.modulepath())
        if hashlib.sha256(dll_path.read_bytes()).hexdigest() != SUPPORTED_DLL:
            memory.close()
            session.detach()
            raise RuntimeError("该播放器 DLL 构建尚未适配，不能套用当前探针地址")
        script.exports_sync.arm()
        print(f"已监听播放器 {pid}，现在打开或继续播放视频。", flush=True)
        # Starting the tool after playback has begun also works when the current session's
        # full playlist and credentials are still resident. Ambiguous snapshots wait for hooks.
        vods, preimages, tokens = memory.snapshot()
        if len(vods) == 1 and len(tokens) == 1:
            matching = [p for p in preimages if set(request_fields(p)[1]).issubset(vods[0]["names"])]
            if not matching:
                matching = saved_preimages(vods[0], args.work)
            if matching:
                def request_time(p):
                    match = re.search(r"(?:^|&)req_time=(\d+)", p)
                    return int(match.group(1)) if match else 0
                inbox.put({"t": "request", "headers": {"authorization": tokens[0]}})
                inbox.put({"t": "kdf", "input": max(matching, key=request_time)})
        token = preimage = None
        try:
            while True:
                try:
                    event = inbox.get(timeout=5)
                except queue.Empty:
                    event = {"t": "poll"}
                if event["t"] == "detached":
                    break
                if event["t"] == "request":
                    token = event["headers"]["authorization"]
                if event["t"] == "kdf":
                    preimage = event["input"]
                if not token or not preimage:
                    continue
                playkey, requested = request_fields(preimage)
                marker = (playkey, requested[0])
                if marker in completed:
                    continue
                try:
                    vod = select_playlist(vods, requested)
                except ValueError:
                    vods = memory.playlists()
                    try:
                        vod = select_playlist(vods, requested)
                    except ValueError:
                        continue
                identity = tuple(vod["names"])
                if identity in completed:
                    completed.add(marker)
                    continue
                print(f"识别完整视频：{len(identity)} 段，{vod['seconds']:.1f} 秒", flush=True)
                try:
                    export(vod, preimage, token, args)
                    completed.update([identity, marker])
                except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
                    if args.once:
                        raise
                    print(f"整课导出失败：{error}；保留缓存，下次播放请求时重试。", flush=True)
                if args.once:
                    return
        finally:
            memory.close()
            try:
                session.detach()
            except frida.InvalidOperationError:
                pass
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path, help="单个视频的输出文件路径")
    parser.add_argument("--directory", type=Path, default=ROOT / "ev-videos", help="自动导出的文件夹")
    parser.add_argument("--container", choices=["mp4", "mkv"], default="mp4")
    parser.add_argument("--work", type=Path, default=ROOT / "tools/parser-tools/captured/full-video")
    parser.add_argument("--cache", type=Path, help="复用播放器下载目录中的密文")
    parser.add_argument("--cli", type=Path, default=ROOT / "target/release/evmedia.exe")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--once", action="store_true", help="导出一个视频后退出")
    parser.add_argument("--from-capture", type=Path, help="复用本机捕获的会话")
    parser.add_argument("--playlist", type=Path, help="与该会话对应的完整原始 M3U8")
    args = parser.parse_args()
    if args.output:
        args.once = True
    for program in [str(args.cli), args.ffmpeg, args.ffprobe]:
        if not shutil.which(program):
            parser.error(f"找不到 {program}")
    if args.from_capture:
        if not args.playlist and not args.pid:
            parser.error("复用捕获时需指定 --pid 或 --playlist")
        from_capture(args)
    else:
        watch(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("监听已停止，已下载的数据保留。")
    except Exception as error:
        print(f"导出失败：{error}", file=sys.stderr)
        sys.exit(1)
