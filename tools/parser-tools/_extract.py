import json, os

SRC = r"C:\Users\Yonjay\.claude\projects\D--Codes-github-ev-reverse\db027b91-1f1e-42d4-a7a3-4a5eb748e4c3.jsonl"
OUT = r"D:\Codes\github\ev-reverse\tools\parser-tools\_session_log.txt"


def clip(s, n):
    s = str(s).replace("\r", "")
    s = " ".join(s.split()) if n <= 200 else s
    return s if len(s) <= n else s[:n] + " …[+%d]" % (len(s) - n)


with open(SRC, encoding="utf-8", errors="replace") as f, open(OUT, "w", encoding="utf-8") as o:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            o.write("[%d] <unparseable line>\n" % i)
            continue
        t = rec.get("type")
        if t not in ("user", "assistant"):
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                txt = (block.get("text") or "").strip()
                if not txt:
                    continue
                o.write("[%d] %s TEXT: %s\n" % (i, t.upper(), clip(txt, 2500)))
            elif bt == "tool_use":
                name = block.get("name")
                inp = block.get("input") or {}
                if name == "Bash":
                    d = clip(inp.get("command", ""), 400)
                elif name in ("Read", "Write", "Edit", "NotebookEdit"):
                    d = str(inp.get("file_path", ""))
                elif name == "Grep":
                    d = "pattern=%r path=%s" % (inp.get("pattern"), inp.get("path", ""))
                elif name == "Glob":
                    d = str(inp.get("pattern", ""))
                elif name in ("Agent", "Task"):
                    d = clip(inp.get("description", ""), 250)
                elif name == "Workflow":
                    d = clip(inp.get("name") or inp.get("description") or "inline script", 250)
                elif name in ("TaskStop", "TaskOutput"):
                    d = str(inp.get("task_id", ""))
                elif name == "Skill":
                    d = str(inp.get("skill", ""))
                else:
                    d = clip(json.dumps(inp, ensure_ascii=False), 250)
                o.write("[%d] TOOL %s: %s\n" % (i, name, d))
            elif bt == "tool_result":
                c = block.get("content")
                if isinstance(c, list):
                    c = "\n".join(
                        (p.get("text") or "") for p in c if isinstance(p, dict) and p.get("type") == "text"
                    )
                c = str(c or "")
                low = c.lower()
                bad = block.get("is_error") or any(
                    k in low
                    for k in ("error", "failed", "fatal", "exception", "traceback", "not found", "denied")
                )
                o.write("[%d] RESULT%s: %s\n" % (i, "(ERR)" if bad else "", clip(c, 220)))
        o.write("")

print("wrote", OUT, os.path.getsize(OUT), "bytes")
