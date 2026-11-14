"""Meta MusicGen running on a warm Google Colab GPU session via the `colab` CLI.

One background worker processes jobs in order on a single named session:
  colab new      -> rent a GPU VM (only when no session is up)
  colab exec     -> load MusicGen once (kernel state persists), then generate per job
  colab download -> pull the WAV back
The session is stopped after COLAB_IDLE_STOP_MINUTES without jobs, and on server shutdown,
so it does not keep burning compute units.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Awaitable, Callable

from .config import ROOT, settings

log = logging.getLogger("jukebox.musicgen")
COLAB_DIR = ROOT / "colab"

TrackUpdate = Callable[..., None]
DoneHook = Callable[[str], Awaitable[None]]


class ColabError(RuntimeError):
    pass


class SessionGone(ColabError):
    pass


async def _colab(*args: str, timeout: float) -> str:
    if not settings.colab_bin:
        raise ColabError("colab CLI not found on PATH (uv tool install google-colab-cli)")
    proc = await asyncio.create_subprocess_exec(
        settings.colab_bin, f"--auth={settings.colab_auth}", *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        raise ColabError(f"colab {args[0]} timed out after {timeout:.0f}s")
    out = raw.decode("utf-8", "replace")
    lowered = out.lower()
    if proc.returncode != 0:
        if "not found" in lowered or "no session" in lowered or " 404" in lowered:
            raise SessionGone(out[-600:])
        raise ColabError(f"colab {args[0]} failed ({proc.returncode}): {out[-600:]}")
    if "traceback (most recent call last)" in lowered:
        raise ColabError(f"remote error: {out[-800:]}")
    return out


class MusicGenWorker:
    def __init__(self, update_track: TrackUpdate, on_done: DoneHook):
        self.update_track = update_track
        self.on_done = on_done
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.session_up = False
        self.model_loaded = False
        self._worker: asyncio.Task | None = None
        self._idle: asyncio.Task | None = None
        self.session = settings.colab_session

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="musicgen-worker")

    async def shutdown(self) -> None:
        for task in (self._idle, self._worker):
            if task:
                task.cancel()
        if self.session_up:
            await self._stop_session("server shutdown")

    def submit(self, code: str, prompt: str) -> None:
        if self._idle:
            self._idle.cancel()
            self._idle = None
        self.update_track(code, "musicgen", status="queued", stage="queued", position=self.queue.qsize() + 1)
        self.queue.put_nowait((code, prompt))

    def status(self) -> dict:
        return {"session": self.session, "session_up": self.session_up, "model_loaded": self.model_loaded,
                "queue": self.queue.qsize(), "gpu": settings.colab_gpu, "model": settings.musicgen_model}

    # ---------- worker ----------
    async def _run(self) -> None:
        while True:
            code, prompt = await self.queue.get()
            try:
                await self._process(code, prompt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - report any failure on the track
                log.exception("MusicGen job %s failed", code)
                self.update_track(code, "musicgen", status="failed", stage="failed", error=str(exc)[:400])
                await self.on_done(code)
            finally:
                self.queue.task_done()
            if self.queue.empty() and self.session_up:
                self._idle = asyncio.create_task(self._idle_stop())

    async def _process(self, code: str, prompt: str) -> None:
        for attempt in (1, 2):
            try:
                await self._ensure_ready(code)
                self.update_track(code, "musicgen", status="working", stage="generating")
                remote = f"/content/out/{code}.wav"
                await self._exec("musicgen_generate.py",
                                 {"prompt": prompt, "seconds": settings.musicgen_seconds, "out": remote},
                                 timeout=1200)
                self.update_track(code, "musicgen", stage="downloading")
                local = settings.audio_dir / f"{code}_musicgen.wav"
                await _colab("download", "--session", self.session, remote, str(local), timeout=300)
                if not local.exists() or local.stat().st_size < 1000:
                    raise ColabError("downloaded MusicGen file is missing or empty")
                from .audio_utils import finalize
                served = await finalize(local)
                self.update_track(code, "musicgen", status="done", stage="done", file=served.name, error=None)
                await self.on_done(code)
                return
            except SessionGone:
                log.warning("Colab session vanished; recreating (attempt %s)", attempt)
                self.session_up = False
                self.model_loaded = False
                if attempt == 2:
                    raise

    async def _ensure_ready(self, code: str) -> None:
        if not self.session_up:
            sessions = await _colab("sessions", timeout=60)
            if self.session in sessions:
                log.info("Reusing existing Colab session %s", self.session)
            else:
                self.update_track(code, "musicgen", status="working", stage="booting")
                await _colab("new", "--session", self.session, "--gpu", settings.colab_gpu, timeout=900)
            self.session_up = True
        if not self.model_loaded:
            self.update_track(code, "musicgen", status="working", stage="loading")
            await self._exec("musicgen_setup.py", {"model": settings.musicgen_model}, timeout=1800)
            self.model_loaded = True

    async def _exec(self, script: str, params: dict, timeout: float) -> str:
        source = (COLAB_DIR / script).read_text(encoding="utf-8")
        header = f"import json\nPARAMS = json.loads({json.dumps(json.dumps(params))})\n"
        tmp = settings.tmp_dir / f"{script.removesuffix('.py')}_{os.getpid()}.py"
        tmp.write_text(header + source, encoding="utf-8")
        out = await _colab("exec", "--session", self.session, "--timeout", str(int(timeout)),
                           "--file", str(tmp), timeout=timeout + 60)
        log.info("colab exec %s -> %s", script, out.strip().splitlines()[-1:] or "")
        return out

    async def _idle_stop(self) -> None:
        try:
            await asyncio.sleep(settings.colab_idle_stop_minutes * 60)
        except asyncio.CancelledError:
            return
        if self.queue.empty() and self.session_up:
            await self._stop_session(f"idle {settings.colab_idle_stop_minutes} min")

    async def _stop_session(self, reason: str) -> None:
        log.info("Stopping Colab session %s (%s)", self.session, reason)
        try:
            await _colab("stop", "--session", self.session, timeout=300)
        except ColabError as exc:
            log.warning("colab stop failed: %s", exc)
        self.session_up = False
        self.model_loaded = False
