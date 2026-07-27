from __future__ import annotations

import pytest

from services.audio_analyzer import AudioPause
from services.scene_sync import plan_scene_boundaries
from services.speech_recognizer import SpeechSegment, SpeechTranscript, SpeechWord
from utils.time_utils import frame_index


def transcript(
    words: list[tuple[str, int, int]],
    segments: list[tuple[str, int, int]] | None = None,
) -> SpeechTranscript:
    return SpeechTranscript(
        "ru",
        tuple(SpeechWord(*word) for word in words),
        tuple(SpeechSegment(*segment) for segment in (segments or [])),
    )


def test_sentence_ends_are_moved_inside_following_pauses() -> None:
    speech = transcript([
        ("Первая.", 500, 1_800),
        ("Вторая?", 2_400, 3_800),
        ("Третья", 4_500, 5_500),
    ])
    pauses = [AudioPause(1_750, 2_250), AudioPause(3_750, 4_250)]

    decisions = plan_scene_boundaries(6_000, 3, pauses, speech)

    assert [decision.kind for decision in decisions] == [
        "sentence_pause",
        "sentence_pause",
    ]
    assert 1_800 <= decisions[0].time_ms <= 2_250
    assert 3_800 <= decisions[1].time_ms <= 4_250


def test_pause_candidate_inside_spoken_word_is_rejected() -> None:
    speech = transcript([
        ("длинное", 1_000, 1_800),
        ("слово", 1_900, 2_500),
        ("дальше", 2_700, 3_300),
    ])

    decisions = plan_scene_boundaries(
        4_000,
        2,
        [AudioPause(1_350, 1_650)],
        speech,
    )

    assert decisions[0].kind == "word_boundary"
    assert not 1_000 < decisions[0].time_ms < 1_800


def test_gap_too_narrow_for_fps_rounding_is_not_treated_as_safe() -> None:
    speech = transcript([
        ("первое.", 100, 1_000),
        ("второе", 1_030, 2_000),
    ])

    with pytest.raises(ValueError, match="Недостаточно безопасных"):
        plan_scene_boundaries(2_200, 2, [], speech)


def test_global_plan_avoids_greedy_dead_end() -> None:
    pauses = [
        AudioPause(2_800, 3_200),
        AudioPause(5_800, 6_200),
        AudioPause(6_200, 6_600),
    ]

    decisions = plan_scene_boundaries(18_000, 3, pauses)

    assert all(decision.kind != "fallback" for decision in decisions)
    assert decisions[0].time_ms < 3_300
    assert 5_700 < decisions[1].time_ms < 6_500


def test_leading_and_trailing_silence_are_not_scene_boundaries() -> None:
    decisions = plan_scene_boundaries(
        10_000,
        2,
        [
            AudioPause(0, 1_000),
            AudioPause(4_000, 4_700),
            AudioPause(9_000, 10_000),
        ],
    )

    assert decisions[0].kind == "long_pause"
    assert 4_000 < decisions[0].time_ms < 4_700


def test_semantic_mode_rejects_more_scenes_than_safe_word_boundaries() -> None:
    speech = transcript([
        ("одно", 100, 500),
        ("два", 700, 1_100),
    ])

    with pytest.raises(ValueError, match="Недостаточно безопасных"):
        plan_scene_boundaries(2_000, 4, [], speech)


def test_exact_uneven_sentence_plan_is_preserved() -> None:
    speech = transcript([
        ("Коротко.", 100, 1_000),
        ("Очень длинная фраза!", 1_200, 11_000),
        ("Финал", 11_100, 11_800),
    ])

    decisions = plan_scene_boundaries(12_000, 3, [], speech)

    assert [item.kind for item in decisions] == ["sentence_end", "sentence_end"]
    assert 1_022 <= decisions[0].time_ms <= 1_178
    assert 11_022 <= decisions[1].time_ms <= 11_078


def test_end_of_final_sentence_is_not_used_as_trailing_silence_transition() -> None:
    speech = transcript([
        ("Первая.", 100, 1_000),
        ("Вторая.", 1_100, 2_000),
        ("Третья.", 2_100, 11_000),
    ])

    decisions = plan_scene_boundaries(12_000, 3, [], speech)

    assert [item.kind for item in decisions] == ["sentence_end", "sentence_end"]
    assert 1_022 <= decisions[0].time_ms <= 1_078
    assert 2_022 <= decisions[1].time_ms <= 2_078
    for fps in (24, 25, 30, 60):
        first_frame_ms = frame_index(decisions[0].time_ms, fps) * 1_000 / fps
        second_frame_ms = frame_index(decisions[1].time_ms, fps) * 1_000 / fps
        assert 1_000 < first_frame_ms < 1_100
        assert 2_000 < second_frame_ms < 2_100


def test_last_returned_segment_is_kept_when_later_words_prove_it_is_internal() -> None:
    speech = transcript(
        [
            ("один", 100, 1_000),
            ("два", 1_500, 2_500),
            ("три", 4_500, 5_500),
        ],
        [("один два", 100, 3_000)],
    )

    decisions = plan_scene_boundaries(6_000, 2, [], speech)

    assert 3_022 <= decisions[0].time_ms <= 4_478
    assert decisions[0].kind == "segment_end"


def test_pause_order_does_not_change_result() -> None:
    pauses = [AudioPause(2_000, 2_600), AudioPause(5_000, 5_700), AudioPause(8_000, 8_800)]
    forward = plan_scene_boundaries(10_000, 3, pauses)
    backward = plan_scene_boundaries(10_000, 3, list(reversed(pauses)))
    assert forward == backward
