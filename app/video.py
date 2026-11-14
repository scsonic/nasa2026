"""Grab still frames from an SVS visualization video for Gemini.

Only the first 30 seconds matter: frames at 0, 6, 12, 18, 24 s plus one at the end of
that window (or the last frame of a shorter video) -> 6 frames, each scaled to fit
within 632x632 (under 640x640) and JPEG encoded. ffmpeg seeks inside the remote mp4 with HTTP range
requests, so the whole video is never downloaded.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger("jukebox.video")

WINDOW = 30.0       # seconds of video to look at
STEP = 6.0          # one frame every STEP seconds from 0
MAX_SIDE = 632      # frames fit within MAX_SIDE x MAX_SIDE (under 640x640)
FRAME_TIMEOUT = 90  # seconds per ffmpeg call
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def available() -> bool:
    return bool(FFMPEG and FFPROBE)


async def _run(*args: str, timeout: float) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-300:].strip() or f"exit {proc.returncode}")
    return out


async def duration(url: str) -> float | None:
    try:
        out = await _run(FFPROBE, "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", url, timeout=30)
        return float(out.decode().strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("ffprobe failed for %s: %s", url, exc)
        return None


def frame_times(dur: float | None) -> list[float]:
    end = min(WINDOW, dur) if dur else WINDOW
    end = max(0.0, end - 0.15)  # a hair before the end so a frame exists there
    times = [t for t in (i * STEP for i in range(int(WINDOW // STEP))) if t < end - 1.0]
    return times + [round(end, 2)]


async def _frame(url: str, t: float) -> bytes:
    scale = f"scale={MAX_SIDE}:{MAX_SIDE}:force_original_aspect_ratio=decrease"
    return await _run(FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", url, "-frames:v", "1",
                      "-vf", scale, "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                      timeout=FRAME_TIMEOUT)


async def extract_frames(url: str) -> list[tuple[float, bytes]]:
    """[(seconds, jpeg bytes), ...] in time order; frames that fail are skipped."""
    if not available():
        log.warning("ffmpeg/ffprobe not found; skipping video analysis")
        return []
    times = frame_times(await duration(url))
    results = await asyncio.gather(*(_frame(url, t) for t in times), return_exceptions=True)
    frames = []
    for t, res in zip(times, results):
        if isinstance(res, BaseException) or not res:
            log.warning("frame at %.1fs failed for %s: %s", t, url, res)
            continue
        frames.append((t, res))
    return frames
