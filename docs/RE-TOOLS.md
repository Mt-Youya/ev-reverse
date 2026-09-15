# The reverse-engineering toolchain this project uses

Recorded so the next session does not have to rediscover where the tools live, what they need, and
which of them are already verified working. None of this is part of the build: `cargo test
--workspace` does not touch any of it.

## Installed

| Tool | Version | Location | State |
| --- | --- | --- | --- |
| x64dbg | snapshot 2026-05-27 (`9c8ca1cae0b6d56cc44f31fddcb10e3b02ffbb87`) | `D:\DevelopmentTools\x64dbg` | installed, runs |
| x64dbg-MCP Server | v1.3 | `D:\DevelopmentTools\x64dbg\release\{x32,x64}\plugins\x64dbg-MCP-Server.dp{32,64}` | **verified**: `initialize` + 80 tools on `127.0.0.1:9094` |
| Ghidra | 12.1.2 | `D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC` | installed, extension deployed |
| ghidra-mcp | 7.0.0 (bridge 1.28.1) | `D:\DevelopmentTools\mcp\ghidra-mcp` | **verified**: stdio bridge registers **238 tools** against Ghidra on `127.0.0.1:8089` |
| Apache Maven | 3.9.11 | `D:\DevelopmentTools\maven\apache-maven-3.9.11` | installed |
| JDK | 21.0.12.1 LTS | `D:\DevelopmentTools\jdks\jdk-21.0.12.1` | already present |
| uv | 0.12.15 | `C:\Users\Yonjay\.conda\envs\subgen\Scripts\uv.exe` | installed for ghidra-mcp |
| Python | 3.13.13 | `C:\Users\Yonjay\.conda\envs\subgen\python.exe` | **not on `PATH`** as `python` |
| agent-browser | 0.19.0 | `C:\Users\Yonjay\.cargo\bin\agent-browser.exe` | installed via cargo; browser automation CLI |

Downloads are kept in `D:\DevelopmentTools\_downloads`. Everything large came down through the
local proxy (`http://127.0.0.1:7897`): direct GitHub was 170 kB/s, the proxy 8.2 MB/s.

## x64dbg, and its MCP server

The plugin is a native x64dbg plugin with no runtime dependency, so installing it is copying two
files. It starts an MCP server **inside x64dbg** on launch, so *x64dbg has to be running* for its
tools to exist:

```
D:\DevelopmentTools\x64dbg\release\x64\x64dbg.exe     # 64-bit targets
D:\DevelopmentTools\x64dbg\release\x32\x32dbg.exe     # 32-bit targets
```

Its settings live in `release\x64\mcp_config.json` (IP, port, `AuthToken`, `AutoStart`); the token
is generated on first run and is required on every request:

```
IP 127.0.0.1, port 9094 (x64) / 9095 (x32)
```

Verified with a real handshake rather than by reading the port list: `initialize` answers as
`x64dbg-MCP Server 1.3` and `tools/list` returns 80 tools — `GetDebugState`, `AttachProcess`,
`LoadBinary`, `ReadMemory`, `SetBreakpoint`, `WaitForEvent`, `EvalExpression`,
`DisassembleFunction`, `SearchForStrings` and so on. That is the same ground the Frida probes in
`tools/parser-tools/` cover by hand, with breakpoints and expression evaluation instead of
`Interceptor.attach` and raw RVAs.

Two things to know when calling it by hand: the body must be sent as a file
(`curl --data-binary @body.json`), because PowerShell mangles an inline JSON argument into a parse
error; and `Accept` must list both `application/json` and `text/event-stream`.

## ghidra-mcp

Prerequisites are Java 21, Maven 3.9+, Ghidra 12.1.2 and Python 3.10+ with `uv`. The repo's own
setup tool is the supported path, and it needs `python`, `mvn` and `uv` on `PATH` plus `JAVA_HOME`:

```powershell
$env:JAVA_HOME = 'D:\DevelopmentTools\jdks\jdk-21.0.12.1'
$env:PATH = "C:\Users\Yonjay\.conda\envs\subgen;C:\Users\Yonjay\.conda\envs\subgen\Scripts;D:\DevelopmentTools\maven\apache-maven-3.9.11\bin;$env:JAVA_HOME\bin;$env:PATH"
cd D:\DevelopmentTools\mcp\ghidra-mcp
python -m tools.setup preflight     --ghidra-path D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC
python -m tools.setup ensure-prereqs --ghidra-path D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC
python -m tools.setup build
python -m tools.setup deploy        --ghidra-path D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC
```

Maven must reach Maven Central, and this machine's route to it is the proxy, configured once in
`%USERPROFILE%\.m2\settings.xml`. Expect an occasional `(bad_record_mac) Tag mismatch` from the
proxy; re-running the build resumes from cache. `deploy` also starts Ghidra and runs health and
schema checks, so it is the one step that opens a window.

Deployed and verified:

```
Ghidra plugin     D:\DevelopmentTools\ghidra\ghidra_12.1.2_PUBLIC\Extensions\Ghidra\GhidraMCP-7.0.0.zip
User extension    %APPDATA%\ghidra\ghidra_12.1.2_PUBLIC\Extensions\GhidraMCP
Bridge (stdio)    D:\DevelopmentTools\mcp\ghidra-mcp\.venv\Scripts\bridge-mcp-ghidra.exe
Ghidra HTTP       http://127.0.0.1:8089   (/check_connection)
```

`deploy`'s last step failed with `No project is currently open`, which is not a defect: the smoke
test wants a program loaded, and a freshly installed Ghidra has no project. `/check_connection`
answers `Connected: GhidraMCP plugin running, but no program loaded`, and the stdio bridge starts,
auto-connects to `127.0.0.1:8089` and registers **238 tools**. Ghidra and its MCP server only exist
while Ghidra is running, and the tools only do something once a program is open.

## Wiring either of them into DSH

DSH bridges MCP servers through its bundled `@deepseek-ai/dsh-mcp-client`, which turns each server's
tools into `mcp__<serverName>__<tool>`. The profile's `cordis.patch.yml`
(`%USERPROFILE%\.dsh\profiles\<profile>\cordis.patch.yml`) is where the entry goes — not
`cordis.yml`, which says so itself at the top:

```yaml
- insert:
    - id: mcp-x64dbg
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: x64dbg
        transport: streamable-http
        url: http://127.0.0.1:9094/
        headers:
          Authorization: 'Bearer <AuthToken from mcp_config.json>'
    - id: mcp-ghidra
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: ghidra
        transport: stdio
        command: D:\DevelopmentTools\mcp\ghidra-mcp\.venv\Scripts\bridge-mcp-ghidra.exe
        toolCallTimeoutMs: 120000
```

The cost is not zero and is worth deciding deliberately: tool definitions go into **every** model
request while the server is registered, and these two are large — x64dbg's `tools/list` is 29 KB of
JSON for 80 tools, and ghidra-mcp registers 238. Registering both puts roughly 320 tool schemas in
every request, so a server that is not needed for the task at hand is better left out, or left
commented out in the patch file.

