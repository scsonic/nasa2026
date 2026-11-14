"""Google Cloud Text-to-Speech: reads one blackboard section aloud.

The text comes from the saved analysis (never from the request), and each record's
section is synthesized once and cached as data/tts/<code>_<section>.mp3.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .lyria import _token

log = logging.getLogger("jukebox.tts")
SECTIONS = ("summary", "video", "listen", "recipe", "part1", "part2")
MAX_CHARS = 4000  # the API takes up to 5000 bytes of input
_locks: dict[str, asyncio.Lock] = {}


class TTSError(RuntimeError):
    pass


def section_text(analysis: dict[str, Any], section: str) -> str:
    a = analysis
    if section == "summary":
        text = a.get("summary", "")
    elif section == "video":
        text = a.get("video_description", "")
    elif section == "listen":
        text = a.get("listening_guide", "")
    elif section == "recipe":
        parts = [a.get("mood") and f"Mood: {a['mood']}.",
                 a.get("tempo_bpm") and f"Tempo: {a['tempo_bpm']} beats per minute.",
                 a.get("musical_key") and f"Key: {a['musical_key']}.",
                 a.get("instruments") and f"Instruments: {', '.join(a['instruments'])}."]
        text = " ".join(p for p in parts if p)
    elif section == "part1":
        text = f"Part 1, by Google Lyria 2. {a.get('lyria_prompt', '')}"
    elif section == "part2":
        text = f"Part 2, by Meta MusicGen. {a.get('musicgen_prompt', '')}"
    else:
        raise ValueError(section)
    return (text or "").strip()[:MAX_CHARS]


async def speech(code: str, section: str, analysis: dict[str, Any]) -> Path:
    """The MP3 for `section` of record `code`, synthesizing it on first use."""
    path = settings.tts_dir / f"{code}_{section}.mp3"
    async with _locks.setdefault(path.name, asyncio.Lock()):
        if path.exists():
            return path
        text = section_text(analysis, section)
        if not text:
            raise TTSError("nothing to read for this section")
        body = {"input": {"text": text},
                "voice": {"languageCode": settings.tts_voice[:5], "name": settings.tts_voice},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": settings.tts_speaking_rate}}
        headers = {"Authorization": f"Bearer {await _token()}", "x-goog-user-project": settings.project}
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post("https://texttospeech.googleapis.com/v1/text:synthesize", json=body, headers=headers)
        if resp.status_code != 200:
            raise TTSError(f"TTS {resp.status_code}: {resp.text[:300]}")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(base64.b64decode(resp.json()["audioContent"]))
        tmp.replace(path)
    _locks.pop(path.name, None)
    return path


async def prefetch(code: str, analysis: dict[str, Any]) -> None:
    """Synthesize every non-empty section in the background; failures are only logged."""
    sections = [s for s in SECTIONS if section_text(analysis, s)]
    results = await asyncio.gather(*(speech(code, s, analysis) for s in sections), return_exceptions=True)
    for section, res in zip(sections, results):
        if isinstance(res, BaseException):
            log.warning("TTS prefetch %s/%s failed: %s", code, section, res)
