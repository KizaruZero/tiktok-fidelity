# TikTok Fidelity

Local-first MP4 preparation for the specific goal of preserving **original encoded media** while testing TikTok upload/transcoding behavior.

> **Important:** this is an empirical interoperability tool, not an official TikTok feature. TikTok controls the server-side transcoding pipeline, so no local tool can guarantee that a particular account, region, client, or future backend will return an unchanged stream.

## Goal

The project is intentionally different from "make the video sharper" tools.

Priority order:

1. **Do not re-encode the user's source by default.**
2. Preserve **native FPS** (e.g. 60 in -> 60 out) as far as the TikTok pipeline permits.
3. Preserve existing **HDR signaling** and Dolby Vision payloads when the source already has them.
4. Only then consider optional quality enhancements later.

A metadata patch does not create HDR/Dolby Vision. It only avoids touching the media bitstream, so existing HDR/DV data stays intact locally.

## Current methods

`inflate ...` is the recommended no-reencode experiment. `patch ... --divisor 2` is retained as an experimental metadata-only variant.

`patch ... --divisor 2` or the automatic 60fps mode implements a byte-preserving MP4 timing-header transformation described publicly by the open-source project [`ut0ku/120fps-method`](https://github.com/ut0ku/120fps-method).

For 60fps it divides `mvhd`/`mdhd` time scale and duration by 2; for 120fps it uses 4. The encoded `mdat` payload is not re-encoded or rewritten.

The project deliberately uses an original Python implementation rather than copying source code from that GPL-3.0 repository.

## Why this is useful

A normal "high quality export" can already be compressed once before TikTok gets it. That is the opposite of the goal here.

This tool therefore gives you a clean experiment:

```text
Editor export (your best source)
        |
        v
TikTok Fidelity patch
        |
        |  NO re-encode
        v
TikTok upload
```

For `patch`, the encoded `mdat` media payload is byte-for-byte identical. For `inflate`, the original encoded payload is preserved and only a tiny trailing dummy `mdat` is appended.

## Install

Python 3.10+:

```bash
python -m pip install -e .
```

FFmpeg/`ffprobe` is optional for the patch itself, but recommended for inspection and verification.

## Commands

Inspect video:

```bash
tiktok-fidelity inspect input.mp4
```

Show JSON:

```bash
tiktok-fidelity inspect input.mp4 --json
```

Recommended experimental path (no local re-encode):

```bash
tiktok-fidelity inflate input.mp4 output.mp4 --multiplier 5 --verify
```

Metadata-only 60/120fps experiment:

```bash
tiktok-fidelity patch input.mp4 output.mp4 --verify
```

Force a divisor when ffprobe is unavailable:

```bash
tiktok-fidelity patch input.mp4 output.mp4 --divisor 2
```

More conservative experiment: patch only the video `mdhd` and leave audio timing metadata alone:

```bash
tiktok-fidelity patch input.mp4 output.mp4 --video-only --verify
```

Compare any two files:

```bash
tiktok-fidelity verify input.mp4 output.mp4
```

## What is verified

The tool can compute hashes over top-level `mdat` payloads. `fps-meta` must keep the media hash identical. `inflate` preserves the original payload and adds a separate tiny dummy `mdat`.

It also compares key video metadata after patching:

- codec / codec tag
- FPS
- resolution
- pixel format
- color space
- transfer function
- color primaries
- Dolby Vision detection

For `patch`, a media-hash change is treated as a hard failure. For `inflate`, the original payload is checked as an exact prefix and the additional bytes are isolated in a new dummy `mdat`.

## HDR / Dolby Vision

If the source already contains HDR or Dolby Vision, a metadata-only patch does not decode or re-encode the video bitstream. That means the existing HDR/DV bytes are preserved locally.

It **cannot** turn SDR into HDR or "trigger Dolby Vision" on a source that does not contain Dolby Vision. Whether TikTok accepts, transcodes, or serves an HDR/DV rendition is controlled server-side.

Use:

```bash
tiktok-fidelity inspect input.mp4
```

to see fields reported by `ffprobe`, including transfer characteristics and Dolby Vision-related metadata when FFmpeg exposes them.

## Research references

- TikTok official media restrictions: MP4 recommended, H.264 recommended, 23-60 FPS, max 4096 px, max 4GB.  
  https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide
- [`ut0ku/120fps-method`](https://github.com/ut0ku/120fps-method) — open-source 60/120fps timing metadata method.
- [`BastienGimbert/tiktok-quality`](https://github.com/BastienGimbert/tiktok-quality) — MIT-licensed MP4 sample-table / "ghost frame" approach for 1080p60.
- [`irgifebry/NoBlur`](https://github.com/irgifebry/NoBlur) — client-side sample-table inflation for TikTok uploads.
- [`MisticGG/fps-method`](https://github.com/MisticGG/fps-method) — local H.265 + container patch approach.
- [`buwryme/tiktok-lossless-upload`](https://github.com/buwryme/tiktok-lossless-upload) — open-source experimental structural-patch approach.

## Status

### v0.1.0

Implemented:

- MP4 ISO-BMFF box parser
- byte-preserving `mvhd` / `mdhd` FPS metadata patch
- sample-table inflation for H.264/H.265-class MP4s without local video re-encoding
- 60fps -> divisor 2
- 120fps -> divisor 4
- optional video-only mode
- `ffprobe`-based inspect
- HDR / Dolby Vision metadata reporting
- `mdat` SHA-256 safety verification
- unit tests

Planned next:

- a GUI / drag-and-drop Windows build
- optional test report generation for comparing TikTok downloads against the pre-upload source
- more rigorous VFR handling
- a test harness for post-upload TikTok downloads and FPS/HDR comparisons
