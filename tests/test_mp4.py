import struct

from tiktok_fidelity.mp4 import find_top, find_child, media_payload_sha256, patch_fps_metadata, read_header_timing


def box(typ: str, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ.encode("latin-1") + payload


def mvhd_v0(timescale: int, duration: int) -> bytes:
    payload = (
        b"\x00\x00\x00\x00"  # version + flags
        + struct.pack(">II", 1, 2)  # creation + modification
        + struct.pack(">II", timescale, duration)
        + b"\x00" * 80
    )
    return box("mvhd", payload)


def mdhd_v0(timescale: int, duration: int) -> bytes:
    payload = (
        b"\x00\x00\x00\x00"
        + struct.pack(">II", 1, 2)
        + struct.pack(">II", timescale, duration)
        + struct.pack(">HH", 0, 0)
    )
    return box("mdhd", payload)


def hdlr(handler: str) -> bytes:
    payload = b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + handler.encode("latin-1") + b"\x00" * 16
    return box("hdlr", payload)


def make_fixture() -> bytes:
    mdia = box("mdia", mdhd_v0(60000, 6000) + hdlr("vide"))
    trak = box("trak", mdia)
    moov = box("moov", mvhd_v0(1000, 10000) + trak)
    mdat_payload = bytes(range(256)) * 4
    return box("ftyp", b"isom\x00\x00\x02\x00isomiso2") + moov + box("mdat", mdat_payload)


def test_fps_patch_preserves_media_payload_and_duration():
    source = make_fixture()
    output, report = patch_fps_metadata(source, divisor=2)

    assert media_payload_sha256(source) == media_payload_sha256(output)
    assert report["video_tracks"] == 1

    moov_src = find_top(source, "moov")
    moov_out = find_top(output, "moov")
    mvhd_src = find_child(source, moov_src, "mvhd")
    mvhd_out = find_child(output, moov_out, "mvhd")
    assert read_header_timing(source, mvhd_src)[1:] == (1000, 10000)
    assert read_header_timing(output, mvhd_out)[1:] == (500, 5000)

    trak_out = find_child(output, moov_out, "trak")
    mdia_out = find_child(output, trak_out, "mdia")
    mdhd_out = find_child(output, mdia_out, "mdhd")
    assert read_header_timing(output, mdhd_out)[1:] == (30000, 3000)


def test_video_only_keeps_audio_if_present():
    # Add a minimal audio track.
    source = make_fixture()
    mdia = box("mdia", mdhd_v0(48000, 4800) + hdlr("soun"))
    audio = box("trak", mdia)
    moov = find_top(source, "moov")
    # Rebuild the moov by replacing its bytes with a version containing audio.
    original_moov = source[moov.start : moov.end]
    original_payload = original_moov[8:]
    new_moov = box("moov", original_payload + audio)
    source2 = source[:moov.start] + new_moov + source[moov.end:]

    output, _ = patch_fps_metadata(source2, divisor=2, patch_audio=False)
    moov_o = find_top(output, "moov")
    traks = [b for b in __import__("tiktok_fidelity.mp4", fromlist=["iter_boxes"]).iter_boxes(output, moov_o.payload_start, moov_o.payload_end) if b.type == "trak"]
    assert len(traks) == 2

    # First track video was patched.
    mdia_video = find_child(output, traks[0], "mdia")
    mdhd_video = find_child(output, mdia_video, "mdhd")
    assert read_header_timing(output, mdhd_video)[1:] == (30000, 3000)

    # Second track audio was intentionally left untouched.
    mdia_audio = find_child(output, traks[1], "mdia")
    mdhd_audio = find_child(output, mdia_audio, "mdhd")
    assert read_header_timing(output, mdhd_audio)[1:] == (48000, 4800)
