"""Generation pipeline: SVS page -> Gemini analysis -> Lyria (now) + MusicGen (background)."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from . import analyze, lyria, svs, tts, video
from .audio_utils import finalize, track_tags
from .config import settings
from .db import DB

log = logging.getLogger("jukebox.jobs")

# Expected seconds per stage; the progress bar eases toward each stage's end.
MAIN_STAGES = {  # stage: (start%, end%, expected seconds)
    "queued": (0, 3, 2),
    "fetching": (3, 10, 4),
    "watching": (10, 25, 30),
    "analyzing": (25, 42, 25),
    "composing": (40, 99, 38),
}
MUSICGEN_STAGES = {
    "queued": (0, 4, 60),
    "booting": (4, 35, 150),
    "loading": (35, 55, 180),
    "generating": (55, 96, 150),
    "downloading": (96, 99, 10),
}
STAGE_LABELS = {
    "queued": "Queued",
    "fetching": "Reading the NASA page",
    "watching": "Jukebox is watching the video",
    "analyzing": "Jukebox is studying the data stream",
    "composing": "Lyria is composing",
    "booting": "Booting a Colab GPU",
    "loading": "Loading MusicGen",
    "generating": "MusicGen is composing",
    "downloading": "Fetching the track",
    "done": "Ready",
    "failed": "Failed",
    "disabled": "Off",
}


def _ease(stages: dict, stage: str | None, started: float | None) -> int:
    if stage == "done":
        return 100
    if stage not in stages or not started:
        return 0
    lo, hi, expected = stages[stage]
    frac = 1 - math.exp(-max(0.0, time.time() - started) / expected)
    return int(lo + (hi - lo) * frac)


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a DB row for the browser (adds progress, URLs, share link)."""
    tracks_out = {}
    for name, t in (row["tracks_json"] or {}).items():
        stage = t.get("stage")
        if name == "lyria":
            # Lyria's progress is the whole main pipeline (fetch -> analyze -> compose).
            progress = 100 if t.get("status") == "done" else _ease(MAIN_STAGES, row["status"], row["stage_started_at"])
            label_stage = "done" if t.get("status") == "done" else (t.get("status") if t.get("status") == "failed" else row["status"])
        else:
            progress = _ease(MUSICGEN_STAGES, stage, t.get("stage_started_at"))
            label_stage = stage
        tracks_out[name] = {
            "status": t.get("status"),
            "stage": label_stage,
            "label": STAGE_LABELS.get(label_stage or "", label_stage),
            "progress": progress,
            "url": f"/media/{t['file']}" if t.get("file") else None,
            "error": t.get("error"),
        }
    return {
        "code": row["code"],
        "share_url": f"{settings.public_base_url}/share/{row['code']}",
        "status": row["status"],
        "stage_label": STAGE_LABELS.get(row["status"], row["status"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "search_term": row["search_term"],
        "svs": row["svs_json"] or {"id": row["svs_id"], "title": row["svs_title"]},
        "analysis": row["analysis_json"],
        "tracks": tracks_out,
    }


class Pipeline:
    def __init__(self, db: DB, musicgen=None):
        self.db = db
        self.musicgen = musicgen
        self._tasks: set[asyncio.Task] = set()

    def initial_tracks(self) -> dict[str, Any]:
        tracks = {"lyria": {"status": "queued", "stage": "queued", "model": settings.lyria_model}}
        if self.musicgen is not None:
            tracks["musicgen"] = {"status": "pending", "stage": "queued", "model": settings.musicgen_model}
        else:
            tracks["musicgen"] = {"status": "disabled", "stage": "disabled", "model": settings.musicgen_model}
        return tracks

    def launch(self, code: str) -> None:
        self._spawn(self.run(code), name=f"gen-{code}")

    def _spawn(self, coro, name: str | None = None) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def snapshot(self, code: str) -> None:
        """Persist the full record of this generation as one JSON document."""
        row = self.db.get(code)
        if not row:
            return
        record = public_view(row)
        record["models"] = {"analysis": settings.gemini_model, "lyria": settings.lyria_model,
                            "musicgen": settings.musicgen_model}
        record["prompts"] = {k: (row["analysis_json"] or {}).get(k) for k in ("lyria_prompt", "negative_prompt", "musicgen_prompt")}
        record["saved_at"] = time.time()
        self.db.update(code, result_json=record)

    async def run(self, code: str) -> None:
        row = self.db.get(code)
        if not row:
            return
        try:
            self.db.set_stage(code, "fetching")
            item = await svs.get_item(row["svs_id"])
            self.db.update(code, svs_json={k: v for k, v in item.items() if k != "image_candidates"},
                           svs_title=item["title"])
            frames: list = []
            if item.get("video_url"):
                # Video stills (first 30 s) and the main image are fetched side by side.
                self.db.set_stage(code, "watching")
                image, frames = await asyncio.gather(svs.fetch_image(item), video.extract_frames(item["video_url"]))
            else:
                image = await svs.fetch_image(item)  # no mp4: skip video, use the page only

            self.db.set_stage(code, "analyzing")
            analysis = await analyze.analyze(item, image, frames)
            self.db.update(code, analysis_json=analysis)
            if settings.tts_prefetch:
                self._spawn(tts.prefetch(code, analysis))

            if self.musicgen is not None:
                self.musicgen.submit(code, analysis["musicgen_prompt"],
                                     track_tags(code, "Part 2", "Meta MusicGen", item["id"], item["title"]))

            self.db.set_stage(code, "composing")
            self.db.update_track(code, "lyria", status="working", stage="composing")
            wav = settings.audio_dir / f"{code}_lyria.wav"
            try:
                await lyria.generate(analysis["lyria_prompt"], analysis.get("negative_prompt", ""), wav)
            except lyria.LyriaError as exc:
                # Retry once with the shorter MusicGen prompt (Lyria sometimes filters long prompts).
                log.warning("Lyria failed for %s (%s); retrying with short prompt", code, exc)
                await lyria.generate(analysis["musicgen_prompt"], "vocals, lyrics", wav)
            served = await finalize(wav, track_tags(code, "Part 1", "Google Lyria 2", item["id"], item["title"]))
            self.db.update_track(code, "lyria", status="done", stage="done", file=served.name, error=None)
            self.db.set_stage(code, "done")
        except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
            log.exception("Generation %s failed", code)
            msg = str(exc)[:400] or exc.__class__.__name__
            self.db.update_track(code, "lyria", status="failed", stage="failed", error=msg)
            current = self.db.get(code)
            if current and (current["tracks_json"] or {}).get("musicgen", {}).get("status") == "pending":
                self.db.update_track(code, "musicgen", status="failed", stage="failed", error="analysis did not finish")
            self.db.set_stage(code, "failed", error=msg)
        finally:
            await self.snapshot(code)
