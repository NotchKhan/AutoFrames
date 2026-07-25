from __future__ import annotations

import json
import os
import shutil
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from utils.time_utils import seconds_to_ms


class MediaProbeError(RuntimeError):
    """Ошибка запуска ffprobe или чтения метаданных."""


def resolve_binary(name: str, explicit_path: str | Path | None = None) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        return None
    env_name = "FFMPEG_BINARY" if name == "ffmpeg" else "FFPROBE_BINARY"
    if os.environ.get(env_name):
        candidate = Path(os.environ[env_name]).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which(name)
    return Path(found).resolve() if found else None


def check_media_tools(
    ffmpeg_path: str | Path | None = None,
    ffprobe_path: str | Path | None = None,
) -> tuple[Path | None, Path | None, list[str]]:
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_path)
    ffprobe = resolve_binary("ffprobe", ffprobe_path)
    errors: list[str] = []
    if ffmpeg is None:
        errors.append(
            "FFmpeg не найден. Установите FFmpeg и добавьте папку bin в PATH либо укажите путь в интерфейсе."
        )
    if ffprobe is None:
        errors.append(
            "ffprobe не найден. Он обычно находится в той же папке bin, что и FFmpeg."
        )
    return ffmpeg, ffprobe, errors


def probe_media(path: Path, ffprobe_path: Path) -> dict[str, Any]:
    command = [
        str(ffprobe_path), "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,duration",
        "-of", "json", str(path),
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True, encoding="utf-8",
            errors="replace", shell=False, creationflags=creation_flags, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaProbeError(f"Не удалось запустить ffprobe для файла «{path.name}»: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "неизвестная ошибка"
        raise MediaProbeError(f"ffprobe не смог прочитать файл «{path.name}»: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MediaProbeError(f"ffprobe вернул некорректные данные для файла «{path.name}».") from exc


def probe_audio_duration_ms(path: Path, ffprobe_path: Path) -> int:
    data = probe_media(path, ffprobe_path)
    audio_streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"]
    if not audio_streams:
        raise MediaProbeError(f"В файле «{path.name}» не найден аудиопоток.")
    duration = data.get("format", {}).get("duration")
    if duration in (None, "N/A"):
        duration = audio_streams[0].get("duration")
    try:
        result = seconds_to_ms(Decimal(str(duration)))
    except (ValueError, InvalidOperation) as exc:
        raise MediaProbeError(f"Не удалось определить длительность аудио «{path.name}».") from exc
    if result <= 0:
        raise MediaProbeError(f"Аудиофайл «{path.name}» имеет нулевую длительность.")
    return result


def summarize_output(path: Path, ffprobe_path: Path) -> dict[str, object]:
    data = probe_media(path, ffprobe_path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    rate = str(video.get("r_frame_rate", "0/1"))
    try:
        numerator, denominator = rate.split("/", maxsplit=1)
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duration_raw = data.get("format", {}).get("duration", "0")
    return {
        "duration_ms": seconds_to_ms(str(duration_raw)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": fps,
        "video_codec": video.get("codec_name", "неизвестно"),
        "audio_codec": audio.get("codec_name", "нет"),
        "size_bytes": path.stat().st_size,
    }
