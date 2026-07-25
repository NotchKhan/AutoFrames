from __future__ import annotations

from pathlib import Path


APP_TITLE = "Автоматическая сборка видео из кадров"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
SYNC_TOLERANCE_MS = 50

PROJECT_ROOT = Path(__file__).resolve().parent
TEMP_ROOT = PROJECT_ROOT / "temp"
OUTPUT_ROOT = PROJECT_ROOT / "output"
LOG_ROOT = PROJECT_ROOT / "logs"

VIDEO_SIZES: dict[str, tuple[int, int]] = {
    "YouTube 16:9 — 1920×1080": (1920, 1080),
    "TikTok / Reels / Shorts 9:16 — 1080×1920": (1080, 1920),
    "Квадрат 1:1 — 1080×1080": (1080, 1080),
}

QUALITY_PRESETS: dict[str, tuple[str, int]] = {
    "Быстрый": ("veryfast", 23),
    "Сбалансированный": ("medium", 20),
    "Высокое качество": ("slow", 18),
}

