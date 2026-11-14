"""Small audio helpers."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .config import settings


def track_tags(code: str, part: str, model: str, svs_id: int, svs_title: str | None) -> dict[str, str]:
    """ID3 tags that say the clip is AI-generated and link back to its share page."""
    share_url = f"{settings.public_base_url}/share/{code}"
    return {
        "title": f"{svs_title or f'NASA SVS No. {svs_id}'} ({part})",
        "artist": f"{model} (AI-generated)",
        "album": "Earth Spins - A NASA Data Jukebox",
        "date": time.strftime("%Y"),
        "comment": (f"AI-generated music by {model}, inspired by NASA SVS visualization No. {svs_id} "
                    f"(https://svs.gsfc.nasa.gov/{svs_id}/). Listen and share: {share_url}"),
        "website": share_url,  # ffmpeg stores unknown keys as TXXX frames
    }


async def finalize(wav_path: Path, tags: dict[str, str] | None = None) -> Path:
    """Optionally transcode WAV to MP3 (smaller to stream and share) with ID3 `tags`.
    Returns the file to serve."""
    if not (settings.transcode_mp3 and settings.ffmpeg_bin):
        return wav_path
    mp3_path = wav_path.with_suffix(".mp3")
    metadata = [arg for k, v in (tags or {}).items() for arg in ("-metadata", f"{k}={v}")]
    proc = await asyncio.create_subprocess_exec(
        settings.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3", *metadata, str(mp3_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not mp3_path.exists():
        return wav_path
    wav_path.unlink(missing_ok=True)
    return mp3_path
