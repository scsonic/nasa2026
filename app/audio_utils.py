"""Small audio helpers."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .config import settings


async def finalize(wav_path: Path) -> Path:
    """Optionally transcode WAV to MP3 (smaller to stream and share). Returns the file to serve."""
    if not (settings.transcode_mp3 and settings.ffmpeg_bin):
        return wav_path
    mp3_path = wav_path.with_suffix(".mp3")
    proc = await asyncio.create_subprocess_exec(
        settings.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not mp3_path.exists():
        return wav_path
    wav_path.unlink(missing_ok=True)
    return mp3_path
