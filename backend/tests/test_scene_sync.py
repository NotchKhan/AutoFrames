from __future__ import annotations

import pytest

from services.audio_analyzer import AudioPause
from services.scene_sync import (
    _maximum_feasible_minimum,
    _prune_planning_candidates,
    _speech_progress_by_candidate,
    build_boundary_candidates,
    plan_scene_boundaries,
)
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


def test_many_images_use_multiple_safe_points_inside_long_internal_pauses() -> None:
    speech = transcript(
        [
            ("Первая.", 500, 1_500),
            ("Вторая.", 6_000, 7_000),
            ("Финал", 10_000, 11_000),
        ],
        [
            ("Первая.", 500, 1_500),
            ("Вторая.", 6_000, 7_000),
        ],
    )

    decisions = plan_scene_boundaries(12_000, 7, [], speech)
    points = [0, *(item.time_ms for item in decisions), 12_000]

    assert len(decisions) == 6
    assert sum(item.kind == "fallback" for item in decisions) >= 2
    assert all(end - start >= 700 for start, end in zip(points, points[1:]))
    assert all(
        not any(word_start < item.time_ms < word_end for _word, word_start, word_end in [
            ("Первая.", 500, 1_500),
            ("Вторая.", 6_000, 7_000),
            ("Финал", 10_000, 11_000),
        ])
        for item in decisions
    )


@pytest.mark.parametrize(
    "words",
    [
        [("первое", 8_000, 8_800), ("финал", 9_200, 9_800)],
        [("первое", 200, 1_000), ("финал", 1_400, 2_000)],
    ],
)
def test_leading_or_trailing_silence_is_never_filled_with_extra_transitions(
    words: list[tuple[str, int, int]],
) -> None:
    speech = transcript(words)

    with pytest.raises(ValueError, match="Недостаточно безопасных"):
        plan_scene_boundaries(10_000, 3, [], speech)


def test_minimum_scene_duration_relaxes_only_when_preferred_value_is_impossible() -> None:
    speech = transcript([
        ("один", 50, 200),
        ("два", 500, 1_000),
        ("три", 1_900, 2_300),
    ])

    decisions = plan_scene_boundaries(2_400, 3, [], speech)
    points = [0, *(item.time_ms for item in decisions), 2_400]
    durations = [end - start for start, end in zip(points, points[1:])]

    assert len(decisions) == 2
    assert durations == [1_180, 640, 580]


def test_strategies_offer_semantic_or_even_word_safe_distribution() -> None:
    speech = transcript([
        ("Раз.", 200, 1_800),
        ("два", 2_000, 3_800),
        ("три", 4_000, 4_900),
        ("четыре", 5_100, 6_800),
        ("Пять.", 7_000, 8_200),
        ("финал", 8_400, 9_500),
    ])

    adaptive = plan_scene_boundaries(10_000, 2, [], speech, strategy="adaptive")
    semantic = plan_scene_boundaries(10_000, 2, [], speech, strategy="semantic")
    even = plan_scene_boundaries(10_000, 2, [], speech, strategy="even")

    assert adaptive[0].kind == "sentence_end"
    assert semantic[0].kind == "sentence_end"
    assert semantic[0].time_ms < 2_000
    assert even[0].kind == "word_boundary"
    assert 4_900 < even[0].time_ms < 5_100
    for decision in [*adaptive, *semantic, *even]:
        assert not any(
            start_ms < decision.time_ms < end_ms
            for _text, start_ms, end_ms in [
                ("Раз.", 200, 1_800),
                ("два", 2_000, 3_800),
                ("три", 4_000, 4_900),
                ("четыре", 5_100, 6_800),
                ("Пять.", 7_000, 8_200),
                ("финал", 8_400, 9_500),
            ]
        )


def test_adaptive_plan_scales_to_many_photos_and_keeps_every_word_intact() -> None:
    words = [
        (f"слово-{index}", index * 1_000 + 100, index * 1_000 + 500)
        for index in range(60)
    ]
    speech = transcript(words)

    decisions = plan_scene_boundaries(60_000, 60, [], speech)
    points = [0, *(item.time_ms for item in decisions), 60_000]

    assert len(decisions) == 59
    assert min(end - start for start, end in zip(points, points[1:])) >= 700
    assert all(
        not any(start_ms < item.time_ms < end_ms for _text, start_ms, end_ms in words)
        for item in decisions
    )


def test_large_transcript_is_pruned_before_global_scoring() -> None:
    scene_count = 500
    duration_ms = 5_000_000
    speech = transcript([
        (f"слово-{index}", index * 500 + 20, index * 500 + 320)
        for index in range(10_000)
    ])
    candidate_spacing_ms = min(700, duration_ms // (scene_count * 3))
    candidates = build_boundary_candidates(
        duration_ms,
        scene_count,
        [],
        speech,
        minimum_gap_ms=candidate_spacing_ms,
    )
    feasible = _maximum_feasible_minimum(
        candidates,
        duration_ms,
        scene_count,
        candidate_spacing_ms,
        700,
    )

    assert feasible is not None
    _minimum_gap_ms, mandatory_path = feasible
    progress = _speech_progress_by_candidate(candidates, speech)
    planning_candidates, _planning_progress = _prune_planning_candidates(
        candidates,
        progress,
        duration_ms,
        scene_count,
        mandatory_path,
    )

    assert len(candidates) > 10_000
    assert len(planning_candidates) <= scene_count * 12
    assert len(plan_scene_boundaries(duration_ms, scene_count, [], speech)) == 499
