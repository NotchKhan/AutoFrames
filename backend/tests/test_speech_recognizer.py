from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from services.speech_recognizer import (
    SpeechRecognitionError,
    ends_sentence,
    parse_transcription_payload,
    transcribe_audio,
)


def test_transcription_payload_parses_words_segments_and_clamps_times() -> None:
    result = parse_transcription_payload({
        "language": "ru",
        "words": [
            {"word": "Привет.", "start": -1, "end": 1.25},
            {"word": "мир", "start": 1.4, "end": 99},
            {"word": "bad", "start": "NaN", "end": 2},
        ],
        "segments": [{"text": "Привет. мир", "start": 0, "end": 3}],
    }, 3_000)

    assert result.language == "ru"
    assert [(word.text, word.start_ms, word.end_ms) for word in result.words] == [
        ("Привет.", 0, 1_250),
        ("мир", 1_400, 3_000),
    ]
    assert result.segments[0].end_ms == 3_000
    assert result.internal_sentence_boundary_count == 1
    assert result.estimated_sentence_count == 2


def test_transcription_without_word_timestamps_is_rejected() -> None:
    with pytest.raises(SpeechRecognitionError, match="метки слов"):
        parse_transcription_payload({
            "segments": [{"text": "Только фраза.", "start": 0, "end": 1}],
        }, 1_000)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Готово.", True),
        ("Готово?»", True),
        ("Почему?", True),
        ("т.е.", False),
        ("2.0", False),
    ],
)
def test_sentence_punctuation(text: str, expected: bool) -> None:
    assert ends_sentence(text) is expected


def test_openai_contract_requests_word_and_segment_timestamps(tmp_path: Path) -> None:
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake-mp3")

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        assert request.headers["authorization"] == "Bearer test-key"
        assert b'timestamp_granularities[]' in body
        assert body.count(b'name="timestamp_granularities[]"') == 2
        assert b'word' in body and b'segment' in body
        assert b'verbose_json' in body and b'whisper-1' in body
        return httpx.Response(200, json={
            "language": "ru",
            "words": [{"word": "Готово.", "start": 0, "end": 1}],
            "segments": [{"text": "Готово.", "start": 0, "end": 1}],
        })

    result = asyncio.run(transcribe_audio(
        audio,
        1_000,
        api_key="test-key",
        base_url="https://api.openai.test/v1",
        transport=httpx.MockTransport(handler),
    ))
    assert result.sentence_end_count == 1


def test_transcription_retries_one_server_error(tmp_path: Path) -> None:
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake-mp3")
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json={
            "words": [{"word": "Да.", "start": 0, "end": 1}],
            "segments": [],
        })

    asyncio.run(transcribe_audio(
        audio,
        1_000,
        api_key="test-key",
        base_url="https://api.openai.test/v1",
        transport=httpx.MockTransport(handler),
    ))
    assert attempts == 2


def test_provider_error_does_not_expose_key_or_response_body(tmp_path: Path) -> None:
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake-mp3")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret provider details")

    with pytest.raises(SpeechRecognitionError) as caught:
        asyncio.run(transcribe_audio(
            audio,
            1_000,
            api_key="test-key",
            base_url="https://api.openai.test/v1",
            transport=httpx.MockTransport(handler),
        ))
    assert "test-key" not in str(caught.value)
    assert "secret provider details" not in str(caught.value)
