from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env", override=False)


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом.") from exc
    if value <= 0:
        raise RuntimeError(f"Переменная {name} должна быть больше нуля.")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть числом.") from exc
    if value <= 0:
        raise RuntimeError(f"Переменная {name} должна быть больше нуля.")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().casefold()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Переменная {name} должна быть true или false.")


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Переменная {name} должна быть целым числом.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"Переменная {name} должна быть в диапазоне от {minimum} до {maximum}."
        )
    return value


APP_TITLE = "AutoFrames API"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/bmp",
    "image/x-ms-bmp",
}
AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
    "audio/ogg",
    "application/ogg",
    "audio/flac",
    "audio/x-flac",
}

SYNC_TOLERANCE_MS = 50
MAX_VIDEO_DIMENSION = 8_192
MAX_IMAGE_PIXELS = 100_000_000

STORAGE_RESERVE_PERCENT = _bounded_int("STORAGE_RESERVE_PERCENT", 10, 0, 50)
SCRATCH_RESERVE_MIN_MB = _positive_int("SCRATCH_RESERVE_MIN_MB", 64)
SCRATCH_RESERVE_MAX_MB = _positive_int("SCRATCH_RESERVE_MAX_MB", 128)
if SCRATCH_RESERVE_MIN_MB < 32:
    raise RuntimeError(
        "Переменная SCRATCH_RESERVE_MIN_MB должна быть не меньше 32."
    )
if SCRATCH_RESERVE_MAX_MB < SCRATCH_RESERVE_MIN_MB:
    raise RuntimeError(
        "Переменная SCRATCH_RESERVE_MAX_MB не может быть меньше SCRATCH_RESERVE_MIN_MB."
    )
SCRATCH_RESERVE_MIN_BYTES = SCRATCH_RESERVE_MIN_MB * 1024 * 1024
SCRATCH_RESERVE_MAX_BYTES = SCRATCH_RESERVE_MAX_MB * 1024 * 1024

MAX_IMAGE_FILES = _positive_int("MAX_IMAGE_COUNT", 500)
MAX_AUDIO_TRACKS = _positive_int("MAX_AUDIO_TRACKS", 20)
MAX_FILE_SIZE_MB = _positive_int("MAX_FILE_SIZE_MB", 100)
MAX_TOTAL_SIZE_MB = _positive_int("MAX_TOTAL_SIZE_MB", 2_048)
MAX_IMAGE_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_AUDIO_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = MAX_TOTAL_SIZE_MB * 1024 * 1024
MAX_TOTAL_FILE_BYTES = MAX_TOTAL_SIZE_MB * 1024 * 1024
PROJECT_TTL_HOURS = _positive_float("PROJECT_TTL_HOURS", 24.0)

# Высокоточная синхронизация использует серверный ключ только на backend.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() or None
OPENAI_API_BASE_URL = os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
OPENAI_TRANSCRIPTION_ENABLED = _boolean("OPENAI_TRANSCRIPTION_ENABLED", False)
OPENAI_TRANSCRIPTION_LANGUAGE = os.getenv("OPENAI_TRANSCRIPTION_LANGUAGE", "").strip() or None
OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS = _positive_float(
    "OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS",
    600.0,
)
OPENAI_TRANSCRIPTION_MAX_MINUTES_PER_HOUR = _positive_float(
    "OPENAI_TRANSCRIPTION_MAX_MINUTES_PER_HOUR",
    120.0,
)
AUDIO_MINIMUM_SCENE_MS = _positive_int("AUDIO_MINIMUM_SCENE_MS", 700)
AUDIO_SILENCE_NOISE_DB = _bounded_int("AUDIO_SILENCE_NOISE_DB", -35, -90, -1)
AUDIO_MINIMUM_SILENCE_MS = _positive_int("AUDIO_MINIMUM_SILENCE_MS", 280)
AUDIO_ANALYSIS_CONCURRENCY = _bounded_int("AUDIO_ANALYSIS_CONCURRENCY", 2, 1, 8)
RENDER_CONCURRENCY = _bounded_int("RENDER_CONCURRENCY", 1, 1, 8)
# Один поток заметно снижает пиковое потребление памяти libx264 на небольших
# облачных инстансах. Разрешение, FPS, CRF и preset при этом не меняются.
FFMPEG_RENDER_THREADS = _bounded_int("FFMPEG_RENDER_THREADS", 1, 1, 8)

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", str(BACKEND_ROOT / "data"))).expanduser().resolve()
TEMP_ROOT = STORAGE_ROOT / "projects"
SCRATCH_ROOT = Path(
    os.getenv("SCRATCH_ROOT", str(STORAGE_ROOT / "scratch"))
).expanduser().resolve()
OUTPUT_ROOT = Path(
    os.getenv("OUTPUT_ROOT", str(STORAGE_ROOT / "output"))
).expanduser().resolve()
LOG_ROOT = STORAGE_ROOT / "logs"

_origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
FRONTEND_ORIGINS = tuple(origin.strip().rstrip("/") for origin in _origins.split(",") if origin.strip())
if not FRONTEND_ORIGINS:
    raise RuntimeError("FRONTEND_ORIGIN должен содержать хотя бы один разрешённый адрес.")

VIDEO_SIZES: dict[str, tuple[int, int]] = {
    "youtube": (1920, 1080),
    "vertical": (1080, 1920),
    "square": (1080, 1080),
}

QUALITY_PRESETS: dict[str, tuple[str, int]] = {
    "fast": ("veryfast", 23),
    "balanced": ("medium", 20),
    "high": ("slow", 18),
}


def ensure_storage_directories() -> None:
    for directory in (TEMP_ROOT, SCRATCH_ROOT, OUTPUT_ROOT, LOG_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
