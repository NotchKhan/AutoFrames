from __future__ import annotations

import asyncio
import math
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


TRANSCRIPTION_ENDPOINT = "/audio/transcriptions"
TRANSCRIPTION_MODEL = "whisper-1"


class SpeechRecognitionError(RuntimeError):
    """Речь не удалось распознать или ответ провайдера оказался некорректным."""


@dataclass(frozen=True, slots=True)
class SpeechWord:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class SpeechTranscript:
    language: str | None
    words: tuple[SpeechWord, ...]
    segments: tuple[SpeechSegment, ...]

    @property
    def sentence_end_count(self) -> int:
        return sum(1 for word in self.words if ends_sentence(word.text))

    @property
    def internal_sentence_boundary_count(self) -> int:
        ordered = sorted(self.words, key=lambda word: (word.start_ms, word.end_ms))
        return sum(1 for word in ordered[:-1] if ends_sentence(word.text))

    @property
    def estimated_sentence_count(self) -> int:
        return self.internal_sentence_boundary_count + (1 if self.words else 0)


def ends_sentence(text: str) -> bool:
    cleaned = text.rstrip().rstrip("\"'»”’)]}")
    if not cleaned.endswith((".", "!", "?", "…")):
        return False
    lowered = cleaned.casefold()
    common_abbreviations = {
        "т.е.", "т.к.", "т.п.", "т.д.", "др.", "г.", "ул.",
        "mr.", "mrs.", "ms.", "dr.", "vs.", "etc.",
    }
    return lowered not in common_abbreviations


def _seconds_to_ms(value: object, duration_ms: int) -> int | None:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds):
        return None
    return min(duration_ms, max(0, round(seconds * 1000)))


def parse_transcription_payload(payload: dict[str, Any], duration_ms: int) -> SpeechTranscript:
    words: list[SpeechWord] = []
    for raw in payload.get("words") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("word") or "").strip()
        start_ms = _seconds_to_ms(raw.get("start"), duration_ms)
        end_ms = _seconds_to_ms(raw.get("end"), duration_ms)
        if text and start_ms is not None and end_ms is not None and end_ms >= start_ms:
            words.append(SpeechWord(text, start_ms, end_ms))

    segments: list[SpeechSegment] = []
    for raw in payload.get("segments") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        start_ms = _seconds_to_ms(raw.get("start"), duration_ms)
        end_ms = _seconds_to_ms(raw.get("end"), duration_ms)
        if text and start_ms is not None and end_ms is not None and end_ms >= start_ms:
            segments.append(SpeechSegment(text, start_ms, end_ms))

    words.sort(key=lambda item: (item.start_ms, item.end_ms))
    segments.sort(key=lambda item: (item.start_ms, item.end_ms))
    if not words:
        raise SpeechRecognitionError(
            "Сервис распознавания не вернул временные метки слов."
        )
    language_raw = payload.get("language")
    language = str(language_raw).strip() if language_raw else None
    return SpeechTranscript(language, tuple(words), tuple(segments))


def _retry_delay_seconds(response: httpx.Response | None) -> float:
    if response is not None:
        try:
            retry_after = float(response.headers.get("retry-after", ""))
        except ValueError:
            retry_after = 0.0
        if math.isfinite(retry_after) and retry_after > 0:
            return min(retry_after, 5.0)
    return 0.35


async def transcribe_audio(
    audio_path: Path,
    audio_duration_ms: int,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    language: str | None = None,
    timeout_seconds: float = 600.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SpeechTranscript:
    """Получает временные метки слов и сегментов; ключ остаётся только на backend."""
    if not api_key.strip():
        raise SpeechRecognitionError("Серверный ключ распознавания не настроен.")
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        raise SpeechRecognitionError("Аудиофайл для распознавания отсутствует или пуст.")
    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme != "https" and parsed_base_url.hostname not in {
        "localhost", "127.0.0.1", "::1",
    }:
        raise SpeechRecognitionError(
            "Адрес сервиса распознавания должен использовать защищённый HTTPS."
        )

    content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    form: dict[str, str | list[str]] = {
        "model": TRANSCRIPTION_MODEL,
        "response_format": "verbose_json",
        "temperature": "0",
        "timestamp_granularities[]": ["word", "segment"],
    }
    if language:
        form["language"] = language

    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 20.0))
    try:
        audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
    except OSError as exc:
        raise SpeechRecognitionError("Не удалось прочитать подготовленную аудиодорожку.") from exc
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                timeout=timeout,
                transport=transport,
            ) as client:
                response = await client.post(
                    TRANSCRIPTION_ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=form,
                    files={"file": (audio_path.name, audio_bytes, content_type)},
                )
        except (OSError, httpx.HTTPError) as exc:
            if attempt == 0:
                await asyncio.sleep(_retry_delay_seconds(None))
                continue
            raise SpeechRecognitionError("Сервис распознавания речи временно недоступен.") from exc
        if response.status_code in {408, 429} or response.status_code >= 500:
            if attempt == 0:
                await asyncio.sleep(_retry_delay_seconds(response))
                continue
        break

    if response is None:
        raise SpeechRecognitionError("Сервис распознавания речи не вернул ответ.")

    if response.status_code in {401, 403}:
        raise SpeechRecognitionError("Серверный ключ распознавания отклонён провайдером.")
    if response.status_code == 413:
        raise SpeechRecognitionError("Аудиофайл слишком большой для сервиса распознавания.")
    if response.status_code == 429:
        raise SpeechRecognitionError("Сервис распознавания временно ограничил число запросов.")
    if response.status_code >= 400:
        raise SpeechRecognitionError(
            f"Сервис распознавания вернул ошибку {response.status_code}."
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SpeechRecognitionError("Сервис распознавания вернул некорректный JSON.") from exc
    if not isinstance(payload, dict):
        raise SpeechRecognitionError("Сервис распознавания вернул неожиданный ответ.")
    return parse_transcription_payload(payload, audio_duration_ms)
