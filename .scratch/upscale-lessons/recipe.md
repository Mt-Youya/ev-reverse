# Recipe: upscaling an exported lesson with the DLSS5 app

An operational note, not a spec: `evmedia` produces the lesson, and
[GUI-DLSS5](https://github.com/JDui/GUI-DLSS5) (the app in
`DLSS5-Neural-Render-v0.1.46-windows-x64.zip`) is what makes it look better afterwards. The two are
separate programs on purpose — this goes in `.scratch/` rather than `docs/` because it documents
somebody else's tool, and it should move to `docs/` only if the workflow becomes standing.

## What the app can and cannot do

| | |
| --- | --- |
| **RTX VSR** (`nvngx_vsr.dll`) | The part that changes size. X1/X2/X4, GPU network upscale. Needs RTX 20-series or newer and a 550+ driver. With it unavailable the app disables upscaling entirely and cannot export above the source resolution. |
| **DLSS-NR** (`nvngx_dlssnr*.dll`) | A generative re-render **at the same resolution**. It regenerates detail, tone and structure from noise and a style setting; it is not an upscaler and it is not a speed-up. Multi-pass (1-5, default 2) runs it again over its own output — slower, and visibly noisier. |
| **RIFE** (`rife_host.dll`, `models/rife-v4.6`) | Frame interpolation, 2x/3x/4x. For a lecture it adds frames nobody needs and multiplies the export. Leave it alone. |
| Order of operations | One pass: **RTX VSR -> DLSS**. Multi-pass with upscaling: **DLSS -> RTX VSR (once) -> remaining DLSS passes**. |

## Readiness on this machine (checked)

- GPU: RTX 4060 (Ada, 8 GiB), driver 617.42. The app selects `nvngx_dlssnr_40.dll`; VSR's 550+ driver
  requirement is met, and the app's own log already says `VSR host ready`.
- H.264 NVENC caps at 4096x4096 in this app. **1080p x4 is 4320 wide, so X4 with H.264 is refused —
  use X2, or switch the encoder to H.265.** The JSON the app exports takes even dimensions.
- Everything the app needs is beside the executable: all three DLSS runtimes, `nvngx_vsr.dll`,
  `rtx_vsr_host.dll`, `rife_host.dll`, `models/rife-v4.6`, `ffmpeg.exe`, `ffprobe.exe`.

## Cost, and which half of it is real

Measured here, not quoted:

| | measured |
| --- | --- |
| The NR network itself, 1080p, on this RTX 4060 | **19.2 ms/frame** minimum (21.6 median) |
| ... at 1440p / 4K | 31.8 ms / 73.8 ms per frame |
| NVENC lossless H.264 encode | 7.7x realtime |
| x264 `-crf 0` encode (the fallback) | 3.6x realtime |

The NR figure comes from `OpenDLSS-NR`'s Vulkan reimplementation of the same 71-block network, not
from the NVIDIA runtime this app loads — treat it as the order of magnitude, not the app's number.
At 20 ms/frame a 60-minute lesson is **36 minutes of NR alone per pass**, before VSR, decode and
encode; the official runtime should beat that, but do not plan a 40-lesson batch on an estimate.
VSR is a much lighter network than NR and should be a small part of the total.

**Therefore: process a one-minute clip of the real lesson first and time it.** The app has no command
line, so the only way to know is to watch one.

## The recipe

1. **Runtime: RTX 40.** Choose it before the first preview — the NGX session is per process, so
   changing it later means restarting the app.
2. **Output size: X2, not X4.** 1080p -> 2160p. X4 costs 4x the pixels through a 20 ms/frame network
   and runs into the H.264 4096 cap for nothing a lecture will show.
3. **Multi-pass: off.** A lesson is text on a flat background — the highest-frequency content there
   is. The NR pass is generative: it can sharpen a stroke and it can invent one, and each extra pass
   adds noise. Judge it in the Compare view at 100% on a page of small text before exporting a batch.
4. **Encoder: H.265 NVENC, CQ around 18-20.** Lossless is available but a lecture does not need it and
   it is ~8 minutes per hour of video; the lossy NVENC path is several times faster.
5. **Keep audio on** (the app re-encodes it to AAC, so the audio is not the lossless part).
6. **RIFE: do not enable.** 2x frames on a 60-minute lesson is 216,000 frames to interpolate.
7. **Verify before trusting it**: `tools/upscale-check.ps1 -Original lesson.mp4 -Enhanced lesson-x2.mp4`
   prints both files' resolution, duration and bitrate, decodes the whole output, and warns when the
   resolution did not actually grow — the failure mode where VSR was unavailable and the export
   quietly stayed at the source size. It also writes two PNGs of the same timestamp to read at 100%,
   which is the one check no script can make.

## What this is not

It has nothing to do with `evmedia`'s own speed. An export's time is the download plus the full
decode validation in `verified_media::publish`; see
[../export-throughput/spec.md](../export-throughput/spec.md) for the numbers. Nothing here makes an
export faster, and DLSS-NR in particular makes everything it touches slower.
