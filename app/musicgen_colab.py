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
import time
from pathlib import Path
from typing import Awaitable, Callable

from .config import ROOT, settings

log = logging.getLogger("jukebox.musicgen")
COLAB_DIR = ROOT / "colab"

TrackUpdate = Callable[..., None]
DoneHook = Callable[[str], Awaitable[None]]


class ColabError(RuntimeError):
    """`str(exc)` is the technical detail for the log; `user_message` is what the player shows."""
    user_message = "MusicGen failed on the Colab GPU. Please try again."

    def __init__(self, detail: str, user_message: str | None = None):
        super().__init__(detail)
        if user_message:
            self.user_message = user_message


class SessionGone(ColabError):
    user_message = "The Colab GPU session ended. Please try again."


class ConnectionLost(ColabError):
    user_message = "Lost the connection to the Colab GPU. Please try again."


class ColabTimeout(ColabError):
    user_message = "The Colab GPU took too long and was stopped. Please try again."


class ColabKilled(ColabError):
    user_message = "The Colab job was stopped before it finished. Please try again."


class RemoteError(ColabError):
    user_message = "MusicGen crashed on the Colab GPU. Please try again."


# The CLI prints this when its kernel websocket drops, but then never exits.
_LOST_MARKER = b"Connection was lost"
# The CLI's own idle limit (--timeout) firing.
_IDLE_MARKER = b"Timeout waiting for"
_TRACEBACK = b"Traceback (most recent call last)"


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


async def _colab(*args: str, timeout: float, done_marker: bytes | None = None) -> str:
    """Run the colab CLI. With `done_marker`, return as soon as the remote script prints it:
    some sessions never deliver the kernel's "finished" reply, and the CLI then waits out its
    whole --timeout even though the work is done."""
    if not settings.colab_bin:
        raise ColabError("colab CLI not found on PATH (uv tool install google-colab-cli)",
                         "MusicGen is not available on this server right now.")
    proc = await asyncio.create_subprocess_exec(
        settings.colab_bin, f"--auth={settings.colab_auth}", *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    buf = bytearray()

    async def drain() -> None:
        while chunk := await proc.stdout.read(4096):
            buf.extend(chunk)

    async def pump() -> str | None:
        """Read until the CLI exits (None) or prints something that settles the result."""
        while chunk := await proc.stdout.read(4096):
            buf.extend(chunk)
            if done_marker and done_marker in buf:
                return "done"
            if _LOST_MARKER in buf:
                return "lost"
            if _TRACEBACK in buf:
                return "traceback"
        await proc.wait()
        return None

    try:
        outcome = await asyncio.wait_for(pump(), timeout)
        if outcome == "traceback":
            # Let the rest of the traceback arrive so we can tell what it was.
            try:
                await asyncio.wait_for(drain(), 3)
            except asyncio.TimeoutError:
                pass
            outcome = "lost" if _LOST_MARKER in buf else "traceback"
    except asyncio.TimeoutError:
        _kill(proc)
        raise ColabTimeout(f"colab {args[0]} timed out after {timeout:.0f}s",
                           f"The Colab GPU took over {timeout / 60:.0f} min and was stopped. Please try again.")
    out = buf.decode("utf-8", "replace")
    if outcome is not None:
        _kill(proc)
        await proc.wait()
    if outcome == "done":
        return out
    if outcome == "lost":
        raise ConnectionLost(f"colab {args[0]}: connection to the Colab kernel was lost")
    if _IDLE_MARKER in buf:
        raise ColabTimeout(f"colab {args[0]}: CLI idle timeout: {out[-400:]}")
    lowered = out.lower()
    if outcome is None and proc.returncode < 0:
        raise ColabKilled(f"colab {args[0]} was killed (signal {-proc.returncode}): {out[-400:]}")
    if outcome is None and proc.returncode != 0:
        if "not found" in lowered or "no session" in lowered or " 404" in lowered:
            raise SessionGone(out[-600:])
        raise ColabError(f"colab {args[0]} failed ({proc.returncode}): {out[-600:]}")
    if "traceback (most recent call last)" in lowered:
        raise RemoteError(f"remote error: {out[-800:]}")
    return out


class MusicGenWorker:
    def __init__(self, update_track: TrackUpdate, on_done: DoneHook):
        self.update_track = update_track
        self.on_done = on_done
        self.queue: asyncio.Queue[tuple[str, str, dict[str, str]]] = asyncio.Queue()
        self.session_up = False
        self.model_loaded = False
        self._worker: asyncio.Task | None = None
        self._idle: asyncio.Task | None = None
        self.session = settings.colab_session
        # Seconds the last successful `colab exec musicgen_generate.py` took, and failure counts.
        self.last_generate_seconds: float | None = None
        self.connection_lost = 0
        self.timeouts = 0

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

    def submit(self, code: str, prompt: str, tags: dict[str, str] | None = None) -> None:
        if self._idle:
            self._idle.cancel()
            self._idle = None
        self.update_track(code, "musicgen", status="queued", stage="queued", position=self.queue.qsize() + 1)
        self.queue.put_nowait((code, prompt, tags or {}))

    def status(self) -> dict:
        return {"session": self.session, "session_up": self.session_up, "model_loaded": self.model_loaded,
                "queue": self.queue.qsize(), "gpu": settings.colab_gpu, "model": settings.musicgen_model,
                "last_generate_seconds": self.last_generate_seconds,
                "timeout_seconds": settings.musicgen_timeout_seconds,
                "connection_lost": self.connection_lost, "timeouts": self.timeouts}

    # ---------- worker ----------
    async def _run(self) -> None:
        while True:
            code, prompt, tags = await self.queue.get()
            try:
                await self._process(code, prompt, tags)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - report any failure on the track
                log.exception("MusicGen job %s failed", code)
                message = getattr(exc, "user_message", "MusicGen failed. Please try again.")
                self.update_track(code, "musicgen", status="failed", stage="failed", error=message)
                await self.on_done(code)
            finally:
                self.queue.task_done()
            if self.queue.empty() and self.session_up:
                self._idle = asyncio.create_task(self._idle_stop())

    async def _process(self, code: str, prompt: str, tags: dict[str, str]) -> None:
        remote = f"/content/out/{code}.wav"
        for attempt in (1, 2):
            try:
                await self._ensure_ready(code)
                self.update_track(code, "musicgen", status="working", stage="generating")
                if attempt == 1 or not await self._remote_exists(remote):
                    started = time.monotonic()
                    try:
                        await self._exec("musicgen_generate.py",
                                         {"prompt": prompt, "seconds": settings.musicgen_seconds, "out": remote},
                                         timeout=settings.musicgen_timeout_seconds, done_marker=b"MG_DONE")
                    except ColabTimeout:
                        self.timeouts += 1
                        raise
                    self.last_generate_seconds = round(time.monotonic() - started, 1)
                    log.info("MusicGen %s generated in %.1fs", code, self.last_generate_seconds)
                self.update_track(code, "musicgen", stage="downloading")
                local = settings.audio_dir / f"{code}_musicgen.wav"
                await _colab("download", "--session", self.session, remote, str(local), timeout=300)
                if not local.exists() or local.stat().st_size < 1000:
                    raise ColabError("downloaded MusicGen file is missing or empty")
                from .audio_utils import finalize
                served = await finalize(local, tags)
                self.update_track(code, "musicgen", status="done", stage="done", file=served.name, error=None)
                await self.on_done(code)
                return
            except SessionGone:
                log.warning("Colab session vanished; recreating (attempt %s)", attempt)
                self.session_up = False
                self.model_loaded = False
                if attempt == 2:
                    raise
            except ConnectionLost:
                # The kernel (and the loaded model) usually survives; retry once on a fresh connection.
                self.connection_lost += 1
                log.warning("Colab connection lost on %s (attempt %s)", code, attempt)
                if attempt == 2:
                    raise

    async def _remote_exists(self, remote: str) -> bool:
        """True if a previous attempt already wrote `remote` before its connection dropped."""
        folder, name = remote.rsplit("/", 1)
        try:
            listing = await _colab("ls", "--session", self.session, folder, timeout=60)
        except ColabError:
            return False
        return name in listing.split()

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
            await self._exec("musicgen_setup.py", {"model": settings.musicgen_model}, timeout=1800,
                             done_marker=b"MG_READY")
            self.model_loaded = True

    async def _exec(self, script: str, params: dict, timeout: float, done_marker: bytes | None = None) -> str:
        source = (COLAB_DIR / script).read_text(encoding="utf-8")
        header = f"import json\nPARAMS = json.loads({json.dumps(json.dumps(params))})\n"
        tmp = settings.tmp_dir / f"{script.removesuffix('.py')}_{os.getpid()}.py"
        tmp.write_text(header + source, encoding="utf-8")
        out = await _colab("exec", "--session", self.session, "--timeout", str(int(timeout)),
                           "--file", str(tmp), timeout=timeout + 60, done_marker=done_marker)
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
