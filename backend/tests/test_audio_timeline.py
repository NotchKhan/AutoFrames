from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from models.timeline import SourceImage
from services.audio_analyzer import (
    AudioPause,
    concatenate_audio_tracks,
    parse_silencedetect_output,
)
from services.speech_recognizer import SpeechTranscript, SpeechWord
from services.timeline_builder import build_audio_timeline


def source(name: str) -> SourceImage:
    return SourceImage(name, Path(name))


def test_silencedetect_output_is_parsed_and_clamped() -> None:
    output = """
[silencedetect @ 1] silence_start: 1.25
[silencedetect @ 1] silence_end: 1.75 | silence_duration: 0.5
[silencedetect @ 1] silence_start: 9.8
"""
    assert parse_silencedetect_output(output, 10_000) == [
        AudioPause(1_250, 1_750),
        AudioPause(9_800, 10_000),
    ]


def test_overlapping_silence_events_are_merged() -> None:
    output = """
[silencedetect @ 1] silence_start: 1.0
[silencedetect @ 1] silence_end: 1.5 | silence_duration: 0.5
[silencedetect @ 2] silence_start: 1.49
[silencedetect @ 2] silence_end: 2.0 | silence_duration: 0.51
"""
    assert parse_silencedetect_output(output, 3_000) == [AudioPause(1_000, 2_000)]


def test_audio_concat_preserves_input_order_and_normalizes_tracks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "01-first.wav"
    second = tmp_path / "02-second.mp3"
    destination = tmp_path / "combined.m4a"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    captured: list[str] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.extend(command)
        destination.write_bytes(b"combined")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("services.audio_analyzer.subprocess.run", run)

    result = concatenate_audio_tracks(
        [first, second],
        Path("ffmpeg"),
        destination,
        8_000,
    )

    assert result == destination
    assert captured.index(str(first)) < captured.index(str(second))
    filter_graph = captured[captured.index("-filter_complex") + 1]
    assert "[0:a:0]aresample=48000" in filter_graph
    assert "[1:a:0]aresample=48000" in filter_graph
    assert "[a0][a1]concat=n=2:v=0:a=1[outa]" in filter_graph
    assert captured[captured.index("-c:a") + 1] == "aac"


def test_audio_timeline_uses_natural_order_and_nearby_pauses() -> None:
    items, issues = build_audio_timeline(
        [source("scene10.jpg"), source("scene2.jpg"), source("scene1.jpg")],
        12_000,
        [AudioPause(3_700, 4_300), AudioPause(7_600, 8_400)],
    )

    assert not issues
    assert [item.original_filename for item in items] == [
        "scene1.jpg",
        "scene2.jpg",
        "scene10.jpg",
    ]
    assert [item.end_ms for item in items] == [3_880, 7_780, 12_000]
    assert items[-1].end_ms == 12_000
    assert all(not item.warnings for item in items)


def test_audio_timeline_falls_back_to_even_boundaries_without_pauses() -> None:
    items, issues = build_audio_timeline(
        [source("01.jpg"), source("02.jpg"), source("03.jpg")],
        9_000,
        [],
    )

    assert not issues
    assert [item.end_ms for item in items] == [3_000, 6_000, 9_000]
    assert [item.parsed_timestamp for item in items] == [
        "равномерно",
        "равномерно",
        "конец аудио",
    ]
    assert items[0].warnings and items[1].warnings
    assert not items[2].warnings


def test_all_matching_pauses_are_kept_for_uneven_scenes() -> None:
    items, issues = build_audio_timeline(
        [source("01.jpg"), source("02.jpg"), source("03.jpg")],
        12_000,
        [AudioPause(700, 1_300), AudioPause(10_700, 11_300)],
    )

    assert not issues
    assert [item.end_ms for item in items] == [880, 10_880, 12_000]


def test_highly_uneven_semantic_scene_is_flagged_without_moving_safe_boundaries() -> None:
    speech = SpeechTranscript(
        "ru",
        (
            SpeechWord("Коротко.", 100, 1_000),
            SpeechWord("Длинно.", 1_200, 11_000),
            SpeechWord("Финал", 11_100, 11_800),
        ),
        (),
    )

    items, issues = build_audio_timeline(
        [source("01.jpg"), source("02.jpg"), source("03.jpg")],
        12_000,
        [],
        speech,
    )

    assert not issues
    assert "значительно длиннее" in " ".join(items[1].warnings)


def test_audio_timeline_exposes_selectable_sync_strategy() -> None:
    speech = SpeechTranscript(
        "ru",
        (
            SpeechWord("Раз.", 200, 1_800),
            SpeechWord("два", 2_000, 3_800),
            SpeechWord("три", 4_000, 4_900),
            SpeechWord("четыре", 5_100, 6_800),
            SpeechWord("Пять.", 7_000, 8_200),
            SpeechWord("финал", 8_400, 9_500),
        ),
        (),
    )

    semantic, semantic_issues = build_audio_timeline(
        [source("01.jpg"), source("02.jpg")],
        10_000,
        [],
        speech,
        strategy="semantic",
    )
    even, even_issues = build_audio_timeline(
        [source("01.jpg"), source("02.jpg")],
        10_000,
        [],
        speech,
        strategy="even",
    )

    assert not semantic_issues and not even_issues
    assert semantic[0].boundary_kind == "sentence_end"
    assert even[0].boundary_kind == "word_boundary"
    assert semantic[0].end_ms < even[0].end_ms


def test_audio_timeline_rejects_more_images_than_physical_frames() -> None:
    items, issues = build_audio_timeline(
        [source(f"{index:03}.jpg") for index in range(61)],
        1_000,
        [],
    )

    assert items == []
    assert len(issues) == 1
    assert "61 изображений" in issues[0].message
    assert "60 FPS" in issues[0].message
    assert "60 физических кадров" in issues[0].message


def test_safe_fallback_has_an_honest_boundary_label() -> None:
    speech = SpeechTranscript(
        "ru",
        (
            SpeechWord("Первая.", 500, 1_500),
            SpeechWord("Вторая.", 6_000, 7_000),
            SpeechWord("Финал", 10_000, 11_000),
        ),
        (),
    )
    items, issues = build_audio_timeline(
        [source(f"{index:02}.jpg") for index in range(1, 8)],
        12_000,
        [],
        speech,
    )

    fallback_items = [item for item in items if item.boundary_kind == "fallback"]
    assert not issues
    assert fallback_items
    assert all(item.parsed_timestamp == "безопасная резервная точка" for item in fallback_items)
    assert all("равномерно" not in item.parsed_timestamp for item in fallback_items)
