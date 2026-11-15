"""The little TV: a short, browser-friendly copy of a record's SVS video.

The music is composed from the first 30 seconds of the video, so the TV only needs that
window. SVS videos are often 4K H.265 (which many browsers cannot play) and tens of MB, so
ffmpeg cuts the first CLIP_SECONDS, scales it to fit 960x540 and encodes small H.264 with no
audio. The clip is cached as data/tv/<svs_id>.mp4 and shared by every record of that ID.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from . import svs, video
from .config import settings

log = logging.getLogger("jukebox.tv")

CLIP_SECONDS = 36      # a little longer than either part (Lyria ~33 s, MusicGen 30 s)
BUILD_TIMEOUT = 300    # seconds for one ffmpeg run
RETRY_AFTER = 600      # seconds before a failed build may be tried again

_tasks: dict[int, asyncio.Task] = {}
_failed: dict[int, tuple[float, str]] = {}
_none: set[int] = set()            # SVS IDs with no mp4 at all
_slots = asyncio.Semaphore(1)      # the server has 2 cores; one transcode at a time


def clip_path(svs_id: int) -> Path:
    return settings.tv_dir / f"{svs_id}.mp4"


async def _build(svs_id: int) -> None:
    out = clip_path(svs_id)
    try:
        item = await svs.get_item(svs_id)
        url = item.get("video_url")
        if not url:
            _none.add(svs_id)
            return
        if not video.available():
            raise RuntimeError("ffmpeg is not installed")
        tmp = out.with_suffix(".part.mp4")
        async with _slots:
            started = time.monotonic()
            await video._run(
                video.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-t", str(CLIP_SECONDS), "-i", url, "-an",
                "-vf", "scale=960:540:force_original_aspect_ratio=decrease:flags=fast_bilinear,"
                       "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(tmp),
                timeout=BUILD_TIMEOUT)
        tmp.replace(out)
        log.info("tv clip for %s ready in %.1fs (%d KB)", svs_id, time.monotonic() - started, out.stat().st_size // 1024)
    except Exception as exc:  # noqa: BLE001
        log.warning("tv clip for %s failed: %s", svs_id, exc)
        _failed[svs_id] = (time.time(), str(exc)[:200])
        out.with_suffix(".part.mp4").unlink(missing_ok=True)
    finally:
        _tasks.pop(svs_id, None)


def ensure(svs_id: int) -> str:
    """Start building the clip if needed; returns ready | building | none | failed."""
    if clip_path(svs_id).exists():
        return "ready"
    if svs_id in _none:
        return "none"
    if svs_id in _tasks:
        return "building"
    failed = _failed.get(svs_id)
    if failed and time.time() - failed[0] < RETRY_AFTER:
        return "failed"
    _failed.pop(svs_id, None)
    _tasks[svs_id] = asyncio.create_task(_build(svs_id), name=f"tv-{svs_id}")
    return "building"
