"""Settings loaded from .env (see .env.example)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "team-bubu")
    gemini_location = os.environ.get("GEMINI_LOCATION", "global")
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    lyria_location = os.environ.get("LYRIA_LOCATION", "us-central1")
    lyria_model = os.environ.get("LYRIA_MODEL", "lyria-002")
    # Google Cloud Text-to-Speech voice for reading the blackboard aloud.
    tts_voice = os.environ.get("TTS_VOICE", "en-US-Chirp3-HD-Aoede")

    musicgen_enabled = _bool("MUSICGEN_ENABLED", True)
    musicgen_model = os.environ.get("MUSICGEN_MODEL", "facebook/musicgen-medium")
    musicgen_seconds = _int("MUSICGEN_SECONDS", 30)
    # Give up on one generation after this long (a normal 30 s clip takes ~80 s on a T4).
    musicgen_timeout_seconds = _int("MUSICGEN_TIMEOUT_SECONDS", 300)
    colab_auth = os.environ.get("COLAB_AUTH", "oauth2")
    colab_gpu = os.environ.get("COLAB_GPU", "T4")
    colab_session = os.environ.get("COLAB_SESSION", "jukebox-musicgen")
    colab_idle_stop_minutes = _int("COLAB_IDLE_STOP_MINUTES", 15)
    colab_bin = shutil.which("colab")

    public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    data_dir = (ROOT / os.environ.get("DATA_DIR", "./data")).resolve()
    # Per client IP: at most RATE_LIMIT_COUNT generations in RATE_LIMIT_WINDOW_MINUTES.
    rate_limit_count = _int("RATE_LIMIT_COUNT", 30)
    rate_limit_window_minutes = _int("RATE_LIMIT_WINDOW_MINUTES", 30)
    transcode_mp3 = _bool("TRANSCODE_MP3", True)
    ffmpeg_bin = shutil.which("ffmpeg")

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def tts_dir(self) -> Path:
        return self.data_dir / "tts"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jukebox.db"


settings = Settings()
settings.audio_dir.mkdir(parents=True, exist_ok=True)
settings.tmp_dir.mkdir(parents=True, exist_ok=True)
settings.tts_dir.mkdir(parents=True, exist_ok=True)
