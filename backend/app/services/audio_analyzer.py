from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


class AudioAnalysisError(RuntimeError):
    """Аудиодорожку не удалось проанализировать на естественные паузы."""


@dataclass(frozen=True, slots=True)
class AudioPause:
    start_ms: int
    end_ms: int

    @property
    def center_ms(self) -> int:
        return self.start_ms + (self.end_ms - self.start_ms) // 2

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def parse_silencedetect_output(output: str, audio_duration_ms: int) -> list[AudioPause]:
    pauses: list[AudioPause] = []
    open_start_ms: int | None = None

    for line in output.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            open_start_ms = max(0, round(float(start_match.group(1)) * 1000))

        end_match = _SILENCE_END_RE.search(line)
        if end_match and open_start_ms is not None:
            end_ms = min(audio_duration_ms, round(float(end_match.group(1)) * 1000))
            if end_ms > open_start_ms:
                pauses.append(AudioPause(open_start_ms, end_ms))
            open_start_ms = None

    if open_start_ms is not None and audio_duration_ms > open_start_ms:
        pauses.append(AudioPause(open_start_ms, audio_duration_ms))
    normalized: list[AudioPause] = []
    for pause in sorted(pauses, key=lambda item: (item.start_ms, item.end_ms)):
        if normalized and pause.start_ms <= normalized[-1].end_ms + 20:
            previous = normalized[-1]
            normalized[-1] = AudioPause(previous.start_ms, max(previous.end_ms, pause.end_ms))
        else:
            normalized.append(pause)
    return normalized


def detect_audio_pauses(
    audio_path: Path,
    ffmpeg_path: Path,
    audio_duration_ms: int,
    *,
    noise_db: int = -35,
    minimum_silence_ms: int = 280,
) -> list[AudioPause]:
    """Находит паузы в речи через FFmpeg silencedetect без облачных сервисов."""
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(audio_path),
        "-map",
        "0:a:0",
        "-vn",
        "-af",
        f"silencedetect=noise={noise_db}dB:d={minimum_silence_ms / 1000:.3f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "null",
        os.devnull,
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    timeout_seconds = max(60, min(900, round(audio_duration_ms / 1000 * 2 + 30)))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creation_flags,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioAnalysisError(f"Не удалось запустить анализ пауз: {exc}") from exc
    if completed.returncode != 0:
        raise AudioAnalysisError("FFmpeg не смог определить паузы в аудиодорожке.")
    return parse_silencedetect_output(completed.stderr, audio_duration_ms)


def prepare_transcription_audio(
    audio_path: Path,
    ffmpeg_path: Path,
    destination: Path,
    audio_duration_ms: int,
) -> Path:
    """Создаёт компактный mono MP3, чтобы не отправлять исходник размером до 100 МБ."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "48k",
        "-n",
        str(destination),
    ]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    timeout_seconds = max(90, min(1800, round(audio_duration_ms / 1000 * 2 + 60)))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creation_flags,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        destination.unlink(missing_ok=True)
        raise AudioAnalysisError("Не удалось подготовить аудио для распознавания.") from exc
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 0:
        destination.unlink(missing_ok=True)
        raise AudioAnalysisError("FFmpeg не смог подготовить дорожку для распознавания.")
    return destination
