from __future__ import annotations

import json
import os
import shutil
import subprocess
from decimal import Decimal, InvalidOperation
from fractions import Fraction
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
    if found:
        return Path(found).resolve()
    local_root = Path(os.environ.get("LOCALAPPDATA", "")) / "AutoVideoBuilder"
    executable = f"{name}.exe" if os.name == "nt" else name
    if local_root.is_dir():
        candidates = sorted(local_root.glob(f"ffmpeg*/**/{executable}"), reverse=True)
        if candidates:
            return candidates[0].resolve()
    return None


def _binary_is_usable(path: Path, expected_name: str) -> bool:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [str(path), "-version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creation_flags,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    first_line = (completed.stdout or completed.stderr).splitlines()
    return (
        completed.returncode == 0
        and bool(first_line)
        and f"{expected_name} version" in first_line[0].lower()
    )


def check_media_tools(
    ffmpeg_path: str | Path | None = None,
    ffprobe_path: str | Path | None = None,
) -> tuple[Path | None, Path | None, list[str]]:
    ffmpeg = resolve_binary("ffmpeg", ffmpeg_path)
    ffprobe = resolve_binary("ffprobe", ffprobe_path)
    errors: list[str] = []
    if ffmpeg is not None and not _binary_is_usable(ffmpeg, "ffmpeg"):
        errors.append(
            "Указанный ffmpeg не прошёл проверку запуска. Выберите настоящий ffmpeg.exe из папки bin."
        )
        ffmpeg = None
    elif ffmpeg is None:
        errors.append(
            "FFmpeg не найден. Установите FFmpeg и добавьте папку bin в PATH либо укажите путь в интерфейсе."
        )
    if ffprobe is not None and not _binary_is_usable(ffprobe, "ffprobe"):
        errors.append(
            "Указанный ffprobe не прошёл проверку запуска. Выберите настоящий ffprobe.exe из папки bin."
        )
        ffprobe = None
    elif ffprobe is None:
        errors.append(
            "ffprobe не найден. Он обычно находится в той же папке bin, что и FFmpeg."
        )
    return ffmpeg, ffprobe, errors


def probe_media(path: Path, ffprobe_path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise MediaProbeError(f"Медиафайл «{path.name}» отсутствует или пуст.")
    command = [
        str(ffprobe_path), "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,"
        "pix_fmt,r_frame_rate,avg_frame_rate,duration",
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
        raise MediaProbeError(
            f"ffprobe не смог прочитать файл «{path.name}». Файл может быть повреждён "
            "или иметь неподдерживаемый формат. Пересохраните его и повторите проверку."
        )
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
    average_rate = str(video.get("avg_frame_rate") or "0/1")
    real_rate = str(video.get("r_frame_rate") or "0/1")
    rate = average_rate if average_rate != "0/1" else real_rate
    try:
        average_fps = float(Fraction(average_rate))
        real_fps = float(Fraction(real_rate))
        fps = average_fps if average_fps > 0 else real_fps
    except (ValueError, ZeroDivisionError):
        average_fps = 0.0
        real_fps = 0.0
        fps = 0.0
    duration_raw = data.get("format", {}).get("duration", "0")
    return {
        "duration_ms": seconds_to_ms(str(duration_raw)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": fps,
        "video_codec": video.get("codec_name", "неизвестно"),
        "audio_codec": audio.get("codec_name", "нет"),
        "pixel_format": video.get("pix_fmt", "неизвестно"),
        # MP4 timescale округляет общую длительность на несколько тиков, поэтому
        # математически одинаковые CFR-потоки могут иметь слегка разные дроби.
        # A one-tick MP4 duration rounding can move avg_frame_rate slightly
        # above 0.001 FPS for short concatenated CFR streams. A 0.01 FPS
        # envelope still rejects common 30/29.97 and 24/23.976 mismatches.
        "is_cfr": real_fps > 0 and abs(average_fps - real_fps) <= 0.01,
        "has_video": bool(video),
        "has_audio": bool(audio),
        "container": data.get("format", {}).get("format_name", "неизвестно"),
        "size_bytes": path.stat().st_size,
    }
