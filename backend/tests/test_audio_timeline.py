from __future__ import annotations

from pathlib import Path

from models.timeline import SourceImage
from services.audio_analyzer import AudioPause, parse_silencedetect_output
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
