"""Google Lyria 2 (Vertex AI) text-to-music: ~30 s instrumental WAV per call."""
from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path

import google.auth
import google.auth.transport.requests
import httpx

from .config import settings

_creds = None
_lock = asyncio.Lock()


async def _token() -> str:
    global _creds
    async with _lock:
        if _creds is None:
            _creds, _ = await asyncio.to_thread(
                google.auth.default, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        if not _creds.valid or (_creds.expiry and _creds.expiry.timestamp() - time.time() < 300):
            await asyncio.to_thread(_creds.refresh, google.auth.transport.requests.Request())
        return _creds.token


class LyriaError(RuntimeError):
    pass


async def generate(prompt: str, negative_prompt: str, out_wav: Path) -> Path:
    loc = settings.lyria_location
    url = (f"https://{loc}-aiplatform.googleapis.com/v1/projects/{settings.project}/locations/{loc}"
           f"/publishers/google/models/{settings.lyria_model}:predict")
    instance = {"prompt": prompt}
    if negative_prompt:
        instance["negative_prompt"] = negative_prompt
    body = {"instances": [instance], "parameters": {"sample_count": 1}}
    headers = {"Authorization": f"Bearer {await _token()}", "x-goog-user-project": settings.project}
    async with httpx.AsyncClient(timeout=240) as http:
        resp = await http.post(url, json=body, headers=headers)
    if resp.status_code != 200:
        try:
            message = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            message = resp.text
        raise LyriaError(f"Lyria {resp.status_code}: {message[:300]}")
    predictions = resp.json().get("predictions") or []
    audio = predictions[0].get("bytesBase64Encoded") if predictions else None
    if not audio:
        raise LyriaError("Lyria returned no audio (the prompt may have been filtered)")
    out_wav.write_bytes(base64.b64decode(audio))
    return out_wav
