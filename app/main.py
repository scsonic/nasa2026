"""Earth Spins (a NASA data jukebox) - FastAPI server.

Run locally:  uv run uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import svs, tts
from .categories import CATEGORIES
from .config import ROOT, settings
from .db import DB
from .jobs import Pipeline, public_view
from .musicgen_colab import MusicGenWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jukebox")

STATIC = ROOT / "static"
CODE_RE = re.compile(r"^[a-z]{5}$")

db = DB(settings.db_path)
musicgen: MusicGenWorker | None = None
pipeline: Pipeline


@asynccontextmanager
async def lifespan(_: FastAPI):
    global musicgen, pipeline
    stale = db.fail_unfinished("server restarted before this finished")
    if stale:
        log.info("Marked %s unfinished generations as failed", stale)

    async def snapshot(code: str) -> None:
        await pipeline.snapshot(code)

    if settings.musicgen_enabled and settings.colab_bin:
        musicgen = MusicGenWorker(db.update_track, snapshot)
        musicgen.start()
    elif settings.musicgen_enabled:
        log.warning("MUSICGEN_ENABLED but the colab CLI is not on PATH; MusicGen disabled")
    pipeline = Pipeline(db, musicgen)
    yield
    if musicgen:
        await musicgen.shutdown()
    await svs.close()


app = FastAPI(title="Earth Spins", lifespan=lifespan)


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """The page, its CSS and its JS must always match: make browsers revalidate them
    (cheap 304 via ETag) instead of mixing a fresh index.html with a stale cached script."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/static/", "/share/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/media", StaticFiles(directory=settings.audio_dir), name="media")


class GenerateRequest(BaseModel):
    svs_id: int = Field(gt=0, lt=10_000_000)
    search_term: str | None = Field(default=None, max_length=80)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/share/{code}", include_in_schema=False)
async def share_page(code: str) -> FileResponse:
    if not CODE_RE.match(code) or not db.get(code):
        raise HTTPException(404, "No such share code")
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "project": settings.project,
        "gemini_model": settings.gemini_model,
        "lyria_model": settings.lyria_model,
        "musicgen": musicgen.status() if musicgen else {"enabled": False},
        "public_base_url": settings.public_base_url,
    }


@app.get("/api/categories")
async def categories() -> list[dict[str, str]]:
    return CATEGORIES


@app.get("/api/search")
async def search(q: str = "", page: int = 0) -> dict:
    """Six results per page ("floor"); EIC pages first, then general SVS."""
    q = q.strip()
    if len(q) > 80:
        raise HTTPException(400, "Query must be at most 80 characters")
    try:
        found = await svs.search(q, max(0, page), per_page=6)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"NASA SVS search failed: {exc}") from exc
    return {"query": q, **found}


@app.get("/api/cover/{svs_id}", include_in_schema=False)
async def cover(svs_id: int) -> RedirectResponse:
    """Redirect to the SVS page's main image (search results don't include one)."""
    try:
        item = await svs.get_item(svs_id)
    except httpx.HTTPError as exc:
        raise HTTPException(404, "No cover") from exc
    if not item.get("image_url"):
        raise HTTPException(404, "No cover")
    return RedirectResponse(item["image_url"], status_code=302, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/svs/{svs_id}")
async def svs_item(svs_id: int) -> dict:
    try:
        item = await svs.get_item(svs_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "SVS item not found") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"NASA SVS request failed: {exc}") from exc
    return {k: v for k, v in item.items() if k != "image_candidates"}


@app.post("/api/generate")
async def generate(body: GenerateRequest, request: Request) -> dict:
    ip = request.client.host if request.client else None
    window = settings.rate_limit_window_minutes * 60
    used, oldest = db.ip_usage_since(ip, time.time() - window)
    if settings.rate_limit_count and used >= settings.rate_limit_count:
        wait = max(60, int((oldest or time.time()) + window - time.time()))
        raise HTTPException(
            429,
            {"message": f"You've tried too many times ({used} plays in {settings.rate_limit_window_minutes} minutes). "
                        f"Give the jukebox a rest and try again in about {-(-wait // 60)} minutes.",
             "retry_after": wait},
            headers={"Retry-After": str(wait)},
        )
    title = None
    try:
        title = (await svs.get_item(body.svs_id))["title"]
    except httpx.HTTPStatusError as exc:
        raise HTTPException(404, f"SVS item {body.svs_id} not found") from exc
    except httpx.HTTPError:
        pass  # the pipeline will retry and report
    code = db.create(body.svs_id, title, body.search_term, pipeline.initial_tracks(), ip)
    pipeline.launch(code)
    return {"code": code, "share_url": f"{settings.public_base_url}/share/{code}"}


@app.get("/api/generations")
async def generation_summaries(codes: str = "") -> list[dict]:
    """Short rows for the share popover's history (the codes live in the browser's cookie)."""
    wanted = [c for c in codes.split(",") if CODE_RE.match(c)][:100]
    return db.summaries(wanted)


@app.get("/api/generations/{code}")
async def generation(code: str) -> JSONResponse:
    if not CODE_RE.match(code):
        raise HTTPException(404, "Bad code")
    row = db.get(code)
    if not row:
        raise HTTPException(404, "No such generation")
    return JSONResponse(public_view(row), headers={"Cache-Control": "no-store"})


@app.get("/api/generations/{code}/record")
async def generation_record(code: str) -> dict:
    """The full JSON saved for this generation (svs data, analysis, prompts, tracks)."""
    row = db.get(code) if CODE_RE.match(code) else None
    if not row:
        raise HTTPException(404, "No such generation")
    return row["result_json"] or public_view(row)


@app.get("/api/tts/{code}/{section}")
async def tts_audio(code: str, section: str) -> FileResponse:
    """One blackboard section of this record, read aloud by Google TTS (MP3, cached)."""
    row = db.get(code) if CODE_RE.match(code) and section in tts.SECTIONS else None
    if not row or not row["analysis_json"]:
        raise HTTPException(404, "Nothing to read")
    try:
        path = await tts.speech(code, section, row["analysis_json"])
    except tts.TTSError as exc:
        log.warning("TTS %s/%s failed: %s", code, section, exc)
        raise HTTPException(502, "Text-to-speech failed")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/recent")
async def recent(limit: int = 20) -> list[dict]:
    return db.recent(max(1, min(limit, 50)))
