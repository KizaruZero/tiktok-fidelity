from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Iterator, Optional


class MP4Error(ValueError):
    """Raised when an ISO-BMFF/MP4 structure cannot be safely parsed."""


@dataclass(frozen=True)
class Box:
    start: int
    size: int
    type: str
    header_size: int

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def payload_start(self) -> int:
        return self.start + self.header_size

    @property
    def payload_end(self) -> int:
        return self.end


def _u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _u64(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">Q", data, offset)[0]


def _p32(value: int) -> bytes:
    return struct.pack(">I", value)


def _p64(value: int) -> bytes:
    return struct.pack(">Q", value)


def iter_boxes(data: bytes | bytearray, start: int, end: int) -> Iterator[Box]:
    """Iterate well-formed ISO-BMFF boxes inside [start, end)."""
    pos = start
    while pos < end:
        if pos + 8 > end:
            raise MP4Error(f"Truncated box header at offset {pos}")
        size32 = _u32(data, pos)
        typ = bytes(data[pos + 4 : pos + 8]).decode("latin-1")
        header = 8
        if size32 == 1:
            if pos + 16 > end:
                raise MP4Error(f"Truncated extended-size box at offset {pos}")
            size = _u64(data, pos + 8)
            header = 16
        elif size32 == 0:
            size = end - pos
        else:
            size = size32
        if size < header or pos + size > end:
            raise MP4Error(
                f"Invalid box {typ!r} at {pos}: size={size}, remaining={end - pos}"
            )
        yield Box(pos, size, typ, header)
        pos += size


def top_level_boxes(data: bytes | bytearray) -> list[Box]:
    return list(iter_boxes(data, 0, len(data)))


def find_child(data: bytes | bytearray, parent: Box, typ: str) -> Optional[Box]:
    for box in iter_boxes(data, parent.payload_start, parent.payload_end):
        if box.type == typ:
            return box
    return None


def find_children(data: bytes | bytearray, parent: Box, typ: str) -> list[Box]:
    return [
        box
        for box in iter_boxes(data, parent.payload_start, parent.payload_end)
        if box.type == typ
    ]


def find_top(data: bytes | bytearray, typ: str) -> Optional[Box]:
    for box in iter_boxes(data, 0, len(data)):
        if box.type == typ:
            return box
    return None


def box_path(data: bytes | bytearray, parent: Box, path: list[str]) -> Optional[Box]:
    current = parent
    for typ in path:
        current = find_child(data, current, typ)
        if current is None:
            return None
    return current


def _full_box_version(data: bytes | bytearray, box: Box) -> int:
    if box.payload_start + 4 > box.end:
        raise MP4Error(f"Box {box.type} is too small to be a FullBox")
    return data[box.payload_start]


def _header_offsets(box: Box, version: int) -> tuple[int, int, int]:
    """Return (timescale_offset, duration_offset, duration_width)."""
    p = box.payload_start
    if version == 0:
        # version/flags, creation_time, modification_time, timescale, duration
        return p + 12, p + 16, 4
    if version == 1:
        # version/flags, creation_time(8), modification_time(8), timescale, duration(8)
        return p + 20, p + 24, 8
    raise MP4Error(f"Unsupported {box.type} version {version}")


def read_header_timing(data: bytes | bytearray, box: Box) -> tuple[int, int, int]:
    """Return (version, timescale, duration) for mvhd/mdhd."""
    version = _full_box_version(data, box)
    ts_off, dur_off, width = _header_offsets(box, version)
    timescale = _u32(data, ts_off)
    duration = _u32(data, dur_off) if width == 4 else _u64(data, dur_off)
    return version, timescale, duration


def patch_timing_box(data: bytearray, box: Box, divisor: int) -> tuple[int, int, int, int]:
    """Divide timescale and duration by divisor, preserving box size and all media bytes."""
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    version = _full_box_version(data, box)
    ts_off, dur_off, width = _header_offsets(box, version)
    old_ts = _u32(data, ts_off)
    old_dur = _u32(data, dur_off) if width == 4 else _u64(data, dur_off)
    new_ts = max(1, old_ts // divisor)
    new_dur = old_dur // divisor
    data[ts_off : ts_off + 4] = _p32(new_ts)
    data[dur_off : dur_off + width] = _p32(new_dur) if width == 4 else _p64(new_dur)
    return old_ts, new_ts, old_dur, new_dur


def track_handler(data: bytes | bytearray, trak: Box) -> Optional[str]:
    mdia = find_child(data, trak, "mdia")
    if not mdia:
        return None
    hdlr = find_child(data, mdia, "hdlr")
    if not hdlr:
        return None
    p = hdlr.payload_start
    if p + 12 > hdlr.end:
        return None
    # FullBox header (4) + pre_defined (4) + handler_type (4)
    raw = bytes(data[p + 8 : p + 12])
    return raw.decode("latin-1")


def media_payload_sha256(data: bytes | bytearray) -> str:
    """Hash every top-level mdat payload in order.

    This deliberately ignores moov/metadata and hashes encoded audio/video
    payload bytes. A metadata-only transformation must leave this unchanged.
    """
    h = hashlib.sha256()
    found = False
    for box in iter_boxes(data, 0, len(data)):
        if box.type == "mdat":
            found = True
            h.update(data[box.payload_start : box.payload_end])
    if not found:
        raise MP4Error("No top-level mdat box found")
    return h.hexdigest()


def patch_fps_metadata(
    input_bytes: bytes,
    *,
    divisor: int,
    patch_audio: bool = True,
) -> tuple[bytes, dict]:
    """Apply the community 60/120-FPS metadata technique without re-encoding.

    The transformation is intentionally conservative: only timing fields in
    mvhd/mdhd boxes are changed. The encoded mdat payload is byte-for-byte
    preserved. This is an empirical TikTok-specific technique, not an official
    TikTok API guarantee.
    """
    data = bytearray(input_bytes)
    moov = find_top(data, "moov")
    if moov is None:
        raise MP4Error("No moov box found")

    mvhd = find_child(data, moov, "mvhd")
    if mvhd is None:
        raise MP4Error("No mvhd box found")

    changed = []
    old_ts, new_ts, old_dur, new_dur = patch_timing_box(data, mvhd, divisor)
    changed.append({
        "box": "mvhd",
        "old_timescale": old_ts,
        "new_timescale": new_ts,
        "old_duration": old_dur,
        "new_duration": new_dur,
    })

    traks = find_children(data, moov, "trak")
    video_count = 0
    mdhd_count = 0
    for trak in traks:
        handler = track_handler(data, trak)
        if handler == "vide":
            video_count += 1
        if not patch_audio and handler != "vide":
            continue
        mdia = find_child(data, trak, "mdia")
        mdhd = find_child(data, mdia, "mdhd") if mdia else None
        if mdhd is None:
            continue
        mdhd_count += 1
        o_ts, n_ts, o_dur, n_dur = patch_timing_box(data, mdhd, divisor)
        changed.append({
            "box": "mdhd",
            "handler": handler or "unknown",
            "old_timescale": o_ts,
            "new_timescale": n_ts,
            "old_duration": o_dur,
            "new_duration": n_dur,
        })

    if video_count == 0:
        raise MP4Error("No video track found")
    if mdhd_count == 0:
        raise MP4Error("No mdhd box found for selected tracks")

    return bytes(data), {
        "divisor": divisor,
        "patched_boxes": changed,
        "video_tracks": video_count,
        "patched_mdhd": mdhd_count,
    }


# ISO-BMFF sample-table inflation used by several public TikTok upload tools.
DUMMY_SIZES = {
    "avc1": 8,
    "avc3": 8,
    "hvc1": 16,
    "hev1": 16,
    "vp09": 4,
    "av01": 4,
    "mp4v": 8,
}


def _find_video_stbl(data: bytes | bytearray, moov: Box) -> tuple[Box, Box, Box, Box]:
    for trak in find_children(data, moov, "trak"):
        mdia = find_child(data, trak, "mdia")
        if not mdia:
            continue
        if track_handler(data, trak) != "vide":
            continue
        minf = find_child(data, mdia, "minf")
        if not minf:
            continue
        stbl = find_child(data, minf, "stbl")
        if stbl:
            return trak, mdia, minf, stbl
    raise MP4Error("No video stbl found")


def _sample_entry_codec(data: bytes | bytearray, stbl: Box) -> str:
    stsd = find_child(data, stbl, "stsd")
    if not stsd or stsd.payload_start + 20 > stsd.end:
        return "unknown"
    # FullBox header (4) + entry_count (4) + sample-entry size (4) + format (4)
    p = stsd.payload_start + 12
    return bytes(data[p : p + 4]).decode("latin-1")


def _parse_stts(data: bytes | bytearray, box: Box) -> tuple[list[tuple[int, int]], int]:
    p = box.payload_start
    if p + 8 > box.end:
        raise MP4Error("Invalid stts box")
    count = _u32(data, p + 4)
    entries: list[tuple[int, int]] = []
    total_samples = 0
    total_duration = 0
    off = p + 8
    for _ in range(count):
        if off + 8 > box.end:
            raise MP4Error("Truncated stts entry")
        n = _u32(data, off)
        delta = _u32(data, off + 4)
        entries.append((n, delta))
        total_samples += n
        total_duration += n * delta
        off += 8
    if total_samples == 0:
        raise MP4Error("stts contains no samples")
    sample_delta = max(1, round(total_duration / total_samples))
    return entries, sample_delta


def _parse_sample_sizes(data: bytes | bytearray, stsz: Box) -> list[int]:
    p = stsz.payload_start
    if p + 12 > stsz.end:
        raise MP4Error("Invalid stsz box")
    sample_size = _u32(data, p + 4)
    count = _u32(data, p + 8)
    if sample_size:
        return [sample_size] * count
    off = p + 12
    out = []
    for _ in range(count):
        if off + 4 > stsz.end:
            raise MP4Error("Truncated stsz entry")
        out.append(_u32(data, off))
        off += 4
    return out


def _parse_chunk_offsets(data: bytes | bytearray, box: Box) -> list[int]:
    p = box.payload_start
    if p + 8 > box.end:
        raise MP4Error("Invalid chunk offset box")
    count = _u32(data, p + 4)
    off = p + 8
    step = 8 if box.type == "co64" else 4
    out: list[int] = []
    for _ in range(count):
        if off + step > box.end:
            raise MP4Error("Truncated chunk offset entry")
        out.append(_u64(data, off) if step == 8 else _u32(data, off))
        off += step
    return out


def _parse_stsc(data: bytes | bytearray, box: Box) -> list[tuple[int, int, int]]:
    p = box.payload_start
    if p + 8 > box.end:
        raise MP4Error("Invalid stsc box")
    count = _u32(data, p + 4)
    out = []
    off = p + 8
    for _ in range(count):
        if off + 12 > box.end:
            raise MP4Error("Truncated stsc entry")
        out.append((_u32(data, off), _u32(data, off + 4), _u32(data, off + 8)))
        off += 12
    return out


def _make_full_box(typ: str, payload: bytes) -> bytes:
    size = 8 + len(payload)
    if size >= (1 << 32):
        raise MP4Error(f"Generated {typ} box is too large")
    return _p32(size) + typ.encode("latin-1") + payload


def _build_stts(real_count: int, delta: int, multiplier: int) -> bytes:
    fake = real_count * (multiplier - 1)
    payload = b"\x00\x00\x00\x00" + _p32(2) + _p32(real_count) + _p32(delta) + _p32(fake) + _p32(delta)
    return _make_full_box("stts", payload)


def _build_stsz(real_sizes: list[int], dummy_size: int, multiplier: int) -> bytes:
    total = len(real_sizes) * multiplier
    body = bytearray(b"\x00" * (4 + 4 + 4))
    body[0:4] = _p32(0)
    body[4:8] = _p32(0)
    body[8:12] = _p32(total)
    payload = bytes(body) + b"".join(_p32(x) for x in real_sizes) + _p32(dummy_size) * (total - len(real_sizes))
    return _make_full_box("stsz", payload)


def _build_stsc(entries: list[tuple[int, int, int]], first_fake_chunk: int) -> bytes:
    payload = bytearray(b"\x00\x00\x00\x00" + _p32(len(entries) + 1))
    for first_chunk, samples_per_chunk, sample_desc in entries:
        payload += _p32(first_chunk) + _p32(samples_per_chunk) + _p32(sample_desc)
    payload += _p32(first_fake_chunk) + _p32(1) + _p32(1)
    return _make_full_box("stsc", bytes(payload))


def _build_chunk_offsets(typ: str, offsets: list[int]) -> bytes:
    payload = bytearray(b"\x00\x00\x00\x00" + _p32(len(offsets)))
    if typ == "co64":
        for off in offsets:
            payload += _p64(off)
    else:
        for off in offsets:
            if off >= (1 << 32):
                raise MP4Error("stco overflow; use co64 input/output for very large files")
            payload += _p32(off)
    return _make_full_box(typ, bytes(payload))


def _replace_boxes(data: bytes, replacements: list[tuple[Box, bytes]]) -> bytes:
    out = bytearray()
    pos = 0
    for box, new_bytes in sorted(replacements, key=lambda x: x[0].start):
        if box.start < pos:
            raise MP4Error("Overlapping replacement boxes")
        out += data[pos:box.start]
        out += new_bytes
        pos = box.end
    out += data[pos:]
    return bytes(out)


def _adjust_ancestor_sizes(buf: bytearray, boxes: list[Box], delta: int) -> None:
    for box in boxes:
        new_size = box.size + delta
        if box.header_size == 8:
            if new_size >= (1 << 32):
                raise MP4Error(f"Box {box.type} would overflow 32-bit size")
            buf[box.start : box.start + 4] = _p32(new_size)
        else:
            buf[box.start + 8 : box.start + 16] = _p64(new_size)


def _adjust_all_chunk_offsets(buf: bytearray, moov: Box, delta: int, skip_video_fake: bool = False) -> None:
    """Add delta to every stco/co64 entry in moov.

    The caller uses this only when moov precedes mdat, so the original media
    chunks move by delta. Fake video entries are created with pre-shifted
    coordinates so they should be included in the same delta pass.
    """
    if delta == 0:
        return

    container_types = {"moov", "trak", "mdia", "minf", "stbl"}

    def walk(box: Box):
        for child in iter_boxes(buf, box.payload_start, box.payload_end):
            if child.type in {"stco", "co64"}:
                p = child.payload_start
                count = _u32(buf, p + 4)
                step = 8 if child.type == "co64" else 4
                off = p + 8
                for _ in range(count):
                    if step == 8:
                        value = _u64(buf, off) + delta
                        buf[off : off + 8] = _p64(value)
                    else:
                        value = _u32(buf, off) + delta
                        if value >= (1 << 32):
                            raise MP4Error("stco overflow after moov relocation")
                        buf[off : off + 4] = _p32(value)
                    off += step
            elif child.type in container_types:
                walk(child)
    walk(moov)


def inflate_video_samples(
    input_bytes: bytes,
    *,
    multiplier: int = 5,
) -> tuple[bytes, dict]:
    """Experimental MP4 sample-table inflation, without video re-encoding.

    The original encoded samples are retained. Extra video samples with tiny
    zero-filled payloads are described in stts/stsz/stsc/stco (or co64) and a
    new mdat is appended. This follows the public structural technique used by
    NoBlur/tiktok-quality, but is implemented independently here.

    This is experimental: TikTok behavior can change, and some desktop/media
    players may dislike the intentionally unusual sample tables.
    """
    if multiplier < 2:
        raise ValueError("multiplier must be >= 2")
    data = bytes(input_bytes)
    top = top_level_boxes(data)
    moov = next((b for b in top if b.type == "moov"), None)
    if moov is None:
        raise MP4Error("No moov box found")
    trak, mdia, minf, stbl = _find_video_stbl(data, moov)

    stts = find_child(data, stbl, "stts")
    stsz = find_child(data, stbl, "stsz")
    stsc = find_child(data, stbl, "stsc")
    stco = find_child(data, stbl, "stco")
    co64 = find_child(data, stbl, "co64")
    if not all((stts, stsz, stsc)) or not (stco or co64):
        raise MP4Error("Video sample tables must include stts, stsz, stsc and stco/co64")

    stts_entries, sample_delta = _parse_stts(data, stts)
    real_count = sum(n for n, _ in stts_entries)
    real_sizes = _parse_sample_sizes(data, stsz)
    if len(real_sizes) != real_count:
        raise MP4Error(f"stsz sample count {len(real_sizes)} != stts sample count {real_count}")

    codec = _sample_entry_codec(data, stbl)
    dummy_size = DUMMY_SIZES.get(codec, 8)
    original_offsets = _parse_chunk_offsets(data, stco or co64)
    original_stsc = _parse_stsc(data, stsc)

    fake_count = real_count * (multiplier - 1)
    new_stts = _build_stts(real_count, sample_delta, multiplier)
    new_stsz = _build_stsz(real_sizes, dummy_size, multiplier)
    new_stsc = _build_stsc(original_stsc, len(original_offsets) + 1)

    chunk_type = "stco" if stco else "co64"
    safe_offset_unshifted = len(data) + 8  # points to payload of new trailing mdat
    fake_offsets = [safe_offset_unshifted] * fake_count
    new_offsets = original_offsets + fake_offsets
    new_chunk = _build_chunk_offsets(chunk_type, new_offsets)

    delta = (
        len(new_stts) - stts.size
        + len(new_stsz) - stsz.size
        + len(new_stsc) - stsc.size
        + len(new_chunk) - (stco or co64).size
    )

    if delta == 0:
        raise MP4Error("Unexpected zero moov delta")

    replacements = [
        (stts, new_stts),
        (stsz, new_stsz),
        (stsc, new_stsc),
        (stco or co64, new_chunk),
    ]
    rebuilt = bytearray(_replace_boxes(data, replacements))
    _adjust_ancestor_sizes(rebuilt, [stbl, minf, mdia, trak, moov], delta)

    # If moov sits before the original media data, all original sample offsets
    # move by the expanded moov. The fake offsets are also shifted here.
    first_mdat = next((b for b in top if b.type == "mdat"), None)
    moov_before_mdat = bool(first_mdat and moov.start < first_mdat.start)
    if moov_before_mdat:
        moov_new = Box(moov.start, moov.size + delta, moov.type, moov.header_size)
        _adjust_all_chunk_offsets(rebuilt, moov_new, delta)

    padding = bytes(dummy_size * fake_count)
    new_mdat_offset = len(rebuilt)
    mdat_size = 8 + len(padding)
    if mdat_size >= (1 << 32):
        # Keep this implementation on the normal 32-bit MP4 box path.
        raise MP4Error("Experimental trailing mdat would exceed 32-bit box size")
    rebuilt += _p32(mdat_size) + b"mdat" + padding

    # The returned fake sample offset should point exactly to the appended mdat payload.
    # Recompute the first fake offset for the report; the actual table already points
    # there after the global relocation pass when needed.
    actual_safe_offset = new_mdat_offset + 8
    return bytes(rebuilt), {
        "multiplier": multiplier,
        "codec": codec,
        "dummy_size": dummy_size,
        "real_frames": real_count,
        "fake_frames": fake_count,
        "moov_delta": delta,
        "fake_mdat_payload_offset": actual_safe_offset,
        "fake_mdat_bytes": len(padding),
        "moov_before_mdat": moov_before_mdat,
    }
