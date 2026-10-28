<#
Run one agent-browser invocation from a JSON spec and leave a sentinel when it is over.

This exists because the caller must not be agent-browser's parent: the CLI keeps a daemon alive,
the daemon stays in the caller's process tree, and every tool call that waits on that tree hangs
even though the output already arrived. The spec file also removes cmd from the path entirely, so
Chinese file names and the spaces inside them need no escaping.

Spec (UTF-8, no BOM), all fields required:
    {
      "exe":     "C:\\Users\\Yonjay\\.cargo\\bin\\agent-browser.exe",
      "args":    ["upload", "input[type=file]", "\"D:\\path with space\\a.mp4\""],
      "log":     "C:\\...\\ab-1234.txt",
      "err":     "C:\\...\\ab-1234.txt.err",
      "done":    "C:\\...\\ab-1234.txt.done",
      "timeout": 120
    }

Arguments are joined with spaces, so an entry that contains a space has to carry its own quotes.
#>
param([Parameter(Mandatory = $true)][string]$Spec)

$ErrorActionPreference = 'Continue'

# Windows PowerShell reads a BOM-less file as ANSI, which would mangle every Chinese path.
$plan = [System.IO.File]::ReadAllText($Spec, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

$started = Start-Process -FilePath $plan.exe -ArgumentList ($plan.args -join ' ') `
    -RedirectStandardOutput $plan.log -RedirectStandardError $plan.err `
    -WindowStyle Hidden -PassThru

$started | Wait-Process -Timeout ([int]$plan.timeout) -ErrorAction SilentlyContinue
if (-not $started.HasExited) {
    Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue
}

[System.IO.File]::WriteAllText($plan.done, 'done', [System.Text.Encoding]::ASCII)
