"""iOS Appium screen-recording helpers.

Appium XCUITest often writes MP4s with mdat size=0 (extends to EOF), which
traps the moov atom inside mdat and makes the file unplayable ("moov atom not
found"). Default codec is also MJPEG, which many players struggle with.
"""

from __future__ import annotations

import base64
import shutil
import struct
import subprocess
from pathlib import Path


def repair_mp4_mdat_size(path: Path) -> bool:
    """If mdat size is 0 (EOF), rewrite it so the following moov is a sibling atom.

    Returns True if the file was modified.
    """
    data = bytearray(path.read_bytes())
    if len(data) < 16:
        return False

    # Walk top-level atoms until mdat.
    off = 0
    mdat_start = None
    while off + 8 <= len(data):
        size = struct.unpack(">I", data[off : off + 4])[0]
        typ = bytes(data[off + 4 : off + 8])
        if typ == b"mdat":
            mdat_start = off
            break
        if size == 1 and off + 16 <= len(data):
            size = struct.unpack(">Q", data[off + 8 : off + 16])[0]
        elif size == 0:
            break
        if size < 8:
            break
        off += size

    if mdat_start is None:
        return False

    declared = struct.unpack(">I", data[mdat_start : mdat_start + 4])[0]
    if declared != 0:
        return False

    moov_type = data.find(b"moov", mdat_start + 8)
    if moov_type < 8:
        return False
    moov_start = moov_type - 4
    new_size = moov_start - mdat_start
    if new_size < 8:
        return False

    struct.pack_into(">I", data, mdat_start, new_size)
    path.write_bytes(data)
    return True


def transcode_to_h264(src: Path, dest: Path) -> bool:
    """Remux/transcode to H.264 + yuv420p for QuickTime / VS Code playback."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return dest.exists() and dest.stat().st_size > 0
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"ffmpeg transcode failed: {exc}")
        return False


def save_recording_bytes(video_b64: str, dest: Path) -> Path:
    """Decode Appium stop_recording_screen payload, repair, and make playable.

    Only the final playable file is kept at ``dest``. Intermediate raw/repaired
    copies are written to a temp dir and deleted afterward.
    """
    import tempfile

    raw = base64.b64decode(video_b64)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="grocery_rec_") as tmp:
        tmp_dir = Path(tmp)
        raw_path = tmp_dir / "raw.mp4"
        working = tmp_dir / "working.mp4"
        raw_path.write_bytes(raw)
        working.write_bytes(raw)

        repaired = repair_mp4_mdat_size(working)
        if repaired:
            print("Repaired MP4 mdat size (temp)")
        else:
            print("MP4 mdat repair not needed / not applicable")

        # Prefer H.264 playable output at the requested dest path.
        if transcode_to_h264(working, dest):
            print(f"Wrote playable H.264 video → {dest}")
            return dest

        # Fallback: use repaired (or raw) as dest.
        shutil.copyfile(working if working.exists() else raw_path, dest)
        print(f"Wrote video (no H.264 transcode) → {dest}")
        return dest


def start_ios_screen_recording(driver, *, time_limit: int = 60) -> None:
    """Start recording. Prefer MJPEG on real devices (libx264 often lacks moov)."""
    attempts = (
        # Default / MJPEG: Appium finalizes a moov we can repair + transcode.
        {"timeLimit": time_limit, "forceRestart": True, "videoType": "mjpeg"},
        {"timeLimit": time_limit, "forceRestart": True},
        {"timeLimit": time_limit, "forceRestart": True, "videoType": "mpeg4"},
        {"timeLimit": time_limit, "forceRestart": True, "videoType": "libx264"},
    )
    last_exc: Exception | None = None
    for opts in attempts:
        try:
            driver.start_recording_screen(**opts)
            print(f"Screen recording started with options={opts}")
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"start_recording_screen failed ({opts}): {exc}")
    raise AssertionError(f"Could not start screen recording: {last_exc}")
