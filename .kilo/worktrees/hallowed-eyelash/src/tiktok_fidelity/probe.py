from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
from typing import Any


class ProbeError(RuntimeError):
    pass


def _run_ffprobe(path: str | Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ProbeError("ffprobe was not found in PATH. Install FFmpeg or pass --no-probe.")
    cmd = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProbeError(proc.stderr.strip() or "ffprobe failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc


def _fps(stream: dict[str, Any]) -> float | None:
    value = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    if not value or value == "0/0":
        return None
    try:
        return float(Fraction(value))
    except Exception:
        return None


def _is_dolby_vision(stream: dict[str, Any]) -> bool:
    codec_tag = str(stream.get("codec_tag_string") or "").lower()
    profile = str(stream.get("profile") or "").lower()
    text = json.dumps(stream, ensure_ascii=False).lower()
    return (
        codec_tag in {"dvh1", "dvhe"}
        or "dolby vision" in profile
        or "dovi" in text
        or "dolby" in text and "vision" in text
    )


def summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ProbeError("No video stream found")

    side_data = video.get("side_data_list") or []
    hdr_side_data_types = [item.get("side_data_type") for item in side_data if item.get("side_data_type")]
    return {
        "format": (probe.get("format") or {}).get("format_name"),
        "duration": (probe.get("format") or {}).get("duration"),
        "size_bytes": (probe.get("format") or {}).get("size"),
        "video": {
            "codec": video.get("codec_name"),
            "codec_tag": video.get("codec_tag_string"),
            "profile": video.get("profile"),
            "level": video.get("level"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": _fps(video),
            "pixel_format": video.get("pix_fmt"),
            "color_range": video.get("color_range"),
            "color_space": video.get("color_space"),
            "color_transfer": video.get("color_transfer"),
            "color_primaries": video.get("color_primaries"),
            "is_hdr_signal": video.get("color_transfer") in {"smpte2084", "arib-std-b67"},
            "is_dolby_vision": _is_dolby_vision(video),
            "hdr_side_data": hdr_side_data_types,
            "tags": video.get("tags") or {},
        },
        "audio": {
            "codec": audio.get("codec_name") if audio else None,
            "sample_rate": audio.get("sample_rate") if audio else None,
            "channels": audio.get("channels") if audio else None,
        },
    }


def probe(path: str | Path) -> dict[str, Any]:
    return summarize_probe(_run_ffprobe(path))


def print_summary(summary: dict[str, Any]) -> None:
    v = summary["video"]
    print(f"Container      : {summary.get('format')}")
    print(f"Resolution     : {v.get('width')}x{v.get('height')}")
    print(f"FPS            : {v.get('fps')}")
    print(f"Codec          : {v.get('codec')} ({v.get('codec_tag')})")
    print(f"Profile/Level  : {v.get('profile')} / {v.get('level')}")
    print(f"Pixel format   : {v.get('pixel_format')}")
    print(f"Color space    : {v.get('color_space')}")
    print(f"Transfer       : {v.get('color_transfer')}")
    print(f"Primaries      : {v.get('color_primaries')}")
    print(f"HDR signal     : {v.get('is_hdr_signal')}")
    print(f"Dolby Vision   : {v.get('is_dolby_vision')}")
    if v.get("hdr_side_data"):
        print(f"HDR side data  : {', '.join(v['hdr_side_data'])}")
    print(f"Audio          : {summary['audio'].get('codec')} {summary['audio'].get('sample_rate')}Hz")
