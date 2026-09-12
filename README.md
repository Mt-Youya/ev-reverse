# evmedia-platform

Rust core for a portable course-video library. It currently implements a compiled command-line core, rather than pretending every platform adapter already exists.

## What works now

- Print catalog trees matching the folder/video hierarchy in the supplied screenshot.
- Download generic HTTP segment manifests concurrently, with per-segment SHA-256 verification and atomic `.part` files.
- Convert EVPlayer2 5.0.5 Windows segment sets using a live-captured manifest from the companion collector already delivered in `../EVPlayer2通用解密工具`.

Build on Windows:

```powershell
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
cargo build --release
.\target\release\evmedia.exe tree .\examples\catalog.json
```

The same core targets macOS, Linux, Android, and iOS. Each native player requires a dedicated adapter that produces an authorized `Catalog` and `DownloadManifest`; the core does not rely on Sandbox.
