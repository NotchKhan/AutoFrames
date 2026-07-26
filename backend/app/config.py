from __future__ import annotations

import os
from pathlib import Path


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
MIN_FREE_RESERVE_BYTES = 512 * 1024 * 1024

MAX_IMAGE_FILES = _positive_int("MAX_IMAGE_COUNT", 500)
MAX_FILE_SIZE_MB = _positive_int("MAX_FILE_SIZE_MB", 100)
MAX_TOTAL_SIZE_MB = _positive_int("MAX_TOTAL_SIZE_MB", 2_048)
MAX_IMAGE_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_AUDIO_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = MAX_TOTAL_SIZE_MB * 1024 * 1024
MAX_TOTAL_FILE_BYTES = MAX_TOTAL_SIZE_MB * 1024 * 1024
PROJECT_TTL_HOURS = _positive_float("PROJECT_TTL_HOURS", 24.0)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", str(BACKEND_ROOT / "data"))).expanduser().resolve()
TEMP_ROOT = STORAGE_ROOT / "projects"
OUTPUT_ROOT = STORAGE_ROOT / "output"
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
    for directory in (TEMP_ROOT, OUTPUT_ROOT, LOG_ROOT):
        directory.mkdir(parents=True, exist_ok=True)
