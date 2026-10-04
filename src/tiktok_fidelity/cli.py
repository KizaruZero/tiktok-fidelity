from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from . import __version__
from .mp4 import MP4Error, media_payload_sha256, patch_fps_metadata, inflate_video_samples
from .probe import ProbeError, probe, print_summary


def _detect_divisor(fps: float | None) -> int:
    if fps is None:
        raise ValueError("Could not determine FPS")
    if abs(fps - 60.0) < 0.01:
        return 2
    if abs(fps - 120.0) < 0.02:
        return 4
    raise ValueError(f"Unsupported FPS for fps-meta method: {fps:.6g}. Use --divisor to override.")


def cmd_inspect(args: argparse.Namespace) -> int:
    summary = probe(args.input)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_summary(summary)
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    src = Path(args.input)
    dst = Path(args.output)
    if src.resolve() == dst.resolve():
        raise ValueError("Output path must be different from input path")
    if not src.exists():
        raise FileNotFoundError(src)

    before = src.read_bytes()
    media_before = media_payload_sha256(before)

    try:
        summary = probe(src)
        fps = summary["video"].get("fps")
    except ProbeError as exc:
        if args.divisor is None:
            raise RuntimeError(str(exc)) from exc
        summary = None
        fps = None

    divisor = args.divisor or _detect_divisor(fps)
    if divisor not in {2, 4}:
        raise ValueError("This project currently supports divisors 2 (60fps) and 4 (120fps).")

    after, report = patch_fps_metadata(
        before,
        divisor=divisor,
        patch_audio=not args.video_only,
    )
    media_after = media_payload_sha256(after)
    if media_before != media_after:
        raise RuntimeError("Safety check failed: encoded mdat payload changed.")

    dst.write_bytes(after)

    print(f"Input          : {src}")
    print(f"Output         : {dst}")
    print(f"Method         : fps-meta (divisor={divisor})")
    if fps is not None:
        print(f"Source FPS     : {fps}")
    print(f"Patched boxes  : {len(report['patched_boxes'])}")
    print(f"Media payload  : IDENTICAL ({media_before[:16]}...) ")
    print("Re-encode      : NO")
    print("HDR/DV payload : PRESERVED at the byte level")
    print("Note           : TikTok-side preservation is empirical and can change server-side.")

    if args.verify:
        _verify_pair(src, dst)
    return 0


def _verify_pair(src: Path, dst: Path) -> None:
    a = src.read_bytes()
    b = dst.read_bytes()
    ha = media_payload_sha256(a)
    hb = media_payload_sha256(b)
    print(f"Verify mdat    : {'UNCHANGED' if ha == hb else 'CHANGED (expected for inflate)'}")

    try:
        sa = probe(src)
        sb = probe(dst)
        keys = [
            ("codec", "codec"),
            ("codec_tag", "codec_tag"),
            ("fps", "fps"),
            ("width", "width"),
            ("height", "height"),
            ("pixel_format", "pixel_format"),
            ("color_space", "color_space"),
            ("color_transfer", "color_transfer"),
            ("color_primaries", "color_primaries"),
            ("is_dolby_vision", "is_dolby_vision"),
        ]
        failed = []
        for label, key in keys:
            if sa["video"].get(key) != sb["video"].get(key):
                failed.append((label, sa["video"].get(key), sb["video"].get(key)))
        if failed:
            print("Metadata      : WARNING")
            for label, old, new in failed:
                print(f"  {label}: {old!r} -> {new!r}")
        else:
            print("Key video meta : PASS")
    except ProbeError as exc:
        print(f"Metadata      : SKIPPED ({exc})")



def _mdat_payloads(data: bytes) -> list[bytes]:
    from .mp4 import iter_boxes
    return [data[b.payload_start:b.payload_end] for b in iter_boxes(data, 0, len(data)) if b.type == "mdat"]


def cmd_inflate(args: argparse.Namespace) -> int:
    src = Path(args.input)
    dst = Path(args.output)
    if src.resolve() == dst.resolve():
        raise ValueError("Output path must be different from input path")
    before = src.read_bytes()
    original_mdat = b"".join(_mdat_payloads(before))
    after, report = inflate_video_samples(before, multiplier=args.multiplier)
    original_again = b"".join(_mdat_payloads(after))
    if not original_again.startswith(original_mdat):
        raise RuntimeError("Safety check failed: original mdat payload was not preserved as a prefix.")
    dst.write_bytes(after)
    print(f"Input          : {src}")
    print(f"Output         : {dst}")
    print(f"Method         : sample-table-inflate (x{args.multiplier})")
    print(f"Codec          : {report['codec']}")
    print(f"Real frames    : {report['real_frames']}")
    print(f"Dummy frames   : {report['fake_frames']}")
    print(f"Extra bytes    : {report['fake_mdat_bytes']}")
    print("Re-encode      : NO")
    print("Original video : PRESERVED; only MP4 tables + tiny dummy mdat added")
    print("HDR/DV payload : PRESERVED at the byte level")
    print("Note           : Experimental TikTok-specific structure; server/player behavior can vary.")
    if args.verify:
        _verify_pair(src, dst)
    return 0

def cmd_verify(args: argparse.Namespace) -> int:
    src = Path(args.input)
    dst = Path(args.output)
    if not src.exists() or not dst.exists():
        raise FileNotFoundError("Both input and output files must exist")
    _verify_pair(src, dst)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tiktok-fidelity",
        description="Prepare MP4s locally without re-encoding the media payload.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("inspect", help="Inspect FPS/HDR/Dolby Vision metadata using ffprobe.")
    sp.add_argument("input")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("patch", help="Patch FPS metadata without re-encoding media.")
    sp.add_argument("input")
    sp.add_argument("output")
    sp.add_argument("--divisor", type=int, help="Override auto divisor: 2 for 60fps, 4 for 120fps")
    sp.add_argument("--video-only", action="store_true", help="Patch only video mdhd instead of video+audio mdhd")
    sp.add_argument("--verify", action="store_true", help="Run post-write verification")
    sp.set_defaults(func=cmd_patch)

    sp = sub.add_parser("inflate", help="Experimental sample-table inflation without video re-encoding.")
    sp.add_argument("input")
    sp.add_argument("output")
    sp.add_argument("--multiplier", type=int, default=5, help="Sample-table multiplier (default: 5)")
    sp.add_argument("--verify", action="store_true", help="Run post-write verification")
    sp.set_defaults(func=cmd_inflate)

    sp = sub.add_parser("verify", help="Verify encoded-media identity and key metadata.")
    sp.add_argument("input")
    sp.add_argument("output")
    sp.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, MP4Error, ProbeError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
