from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from math import inf
from typing import Literal, Sequence

from services.audio_analyzer import AudioPause
from services.speech_recognizer import SpeechTranscript, ends_sentence


BoundaryKind = Literal[
    "sentence_pause",
    "sentence_end",
    "segment_pause",
    "segment_end",
    "long_pause",
    "short_pause",
    "word_boundary",
    "fallback",
]
SyncStrategy = Literal["adaptive", "semantic", "even"]


_KIND_PENALTY: dict[BoundaryKind, float] = {
    "sentence_pause": 0.0,
    "sentence_end": 0.25,
    "segment_pause": 0.8,
    "segment_end": 1.15,
    "long_pause": 1.5,
    "short_pause": 2.2,
    "word_boundary": 4.0,
    "fallback": 10.0,
}
_KIND_PRIORITY = {kind: -penalty for kind, penalty in _KIND_PENALTY.items()}
# При 24 FPS округление к ближайшему кадру может сдвинуть границу на 20,84 мс.
# Запас 22 мс сохраняет смену между словами при любом поддерживаемом FPS.
_FRAME_ROUNDING_GUARD_MS = 22
_MAX_CANDIDATE_MERGE_MS = 90
_TIME_BALANCE_WEIGHT = 0.9
_SPEECH_BALANCE_WEIGHT = 0.7
_MAX_PLANNING_CANDIDATES_PER_SCENE = 12

_STRATEGY_KIND_MULTIPLIER: dict[SyncStrategy, float] = {
    "adaptive": 1.0,
    "semantic": 1.45,
    "even": 0.0,
}
_STRATEGY_BALANCE_WEIGHTS: dict[SyncStrategy, tuple[float, float]] = {
    "adaptive": (_TIME_BALANCE_WEIGHT, _SPEECH_BALANCE_WEIGHT),
    "semantic": (0.5, 0.65),
    "even": (2.6, 0.1),
}


@dataclass(frozen=True, slots=True)
class BoundaryCandidate:
    time_ms: int
    kind: BoundaryKind


@dataclass(frozen=True, slots=True)
class BoundaryDecision:
    time_ms: int
    kind: BoundaryKind


def _pause_after(
    moment_ms: int,
    pauses: Sequence[AudioPause],
    pause_starts: Sequence[int],
    duration_ms: int,
) -> AudioPause | None:
    insertion = bisect_right(pause_starts, moment_ms)
    indexes: list[int] = []
    if insertion:
        indexes.append(insertion - 1)
    index = insertion
    while index < len(pauses) and pauses[index].start_ms - moment_ms <= 650:
        indexes.append(index)
        index += 1
    matches = []
    for index in indexes:
        pause = pauses[index]
        if (
            pause.start_ms > 120
            and pause.end_ms < duration_ms - 120
            and (
                pause.start_ms - 250 <= moment_ms <= pause.end_ms - 20
                or 0 <= pause.start_ms - moment_ms <= 650
            )
        ):
            matches.append(pause)
    if not matches:
        return None
    return min(matches, key=lambda pause: (abs(pause.center_ms - moment_ms), -pause.duration_ms))


def _cut_inside_pause(pause: AudioPause, after_ms: int | None = None) -> int | None:
    guard_ms = min(90, max(1, pause.duration_ms // 5))
    safe_start = pause.start_ms + guard_ms
    safe_end = max(safe_start, pause.end_ms - guard_ms)
    preferred = pause.start_ms + min(180, max(guard_ms, pause.duration_ms // 3))
    if after_ms is not None:
        if safe_end < after_ms + _FRAME_ROUNDING_GUARD_MS:
            return None
        preferred = max(preferred, after_ms + 35)
    return min(safe_end, max(safe_start, preferred))


def _word_intervals(transcript: SpeechTranscript) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for word in sorted(transcript.words, key=lambda item: (item.start_ms, item.end_ms)):
        if word.end_ms <= word.start_ms:
            continue
        if intervals and word.start_ms <= intervals[-1][1]:
            start_ms, end_ms = intervals[-1]
            intervals[-1] = (start_ms, max(end_ms, word.end_ms))
        else:
            intervals.append((word.start_ms, word.end_ms))
    return intervals


def _move_out_of_word(
    moment_ms: int,
    intervals: Sequence[tuple[int, int]],
    starts: Sequence[int],
) -> int:
    index = bisect_right(starts, moment_ms) - 1
    if index >= 0 and intervals[index][0] < moment_ms < intervals[index][1]:
        return intervals[index][1]
    return moment_ms


def _has_word_guard(
    moment_ms: int,
    intervals: Sequence[tuple[int, int]],
    starts: Sequence[int],
) -> bool:
    next_index = bisect_right(starts, moment_ms)
    if next_index <= 0 or next_index >= len(intervals):
        return False
    previous = intervals[next_index - 1]
    following = intervals[next_index]
    return (
        moment_ms - previous[1] >= _FRAME_ROUNDING_GUARD_MS
        and following[0] - moment_ms >= _FRAME_ROUNDING_GUARD_MS
    )


def _next_speech_start(after_ms: int, starts: Sequence[int]) -> int | None:
    index = bisect_left(starts, after_ms)
    return starts[index] if index < len(starts) else None


def _safe_interword_cut(after_ms: int, next_start_ms: int | None) -> int | None:
    if next_start_ms is None:
        return None
    safe_start = after_ms + _FRAME_ROUNDING_GUARD_MS
    safe_end = next_start_ms - _FRAME_ROUNDING_GUARD_MS
    if safe_start > safe_end:
        return None
    preferred = after_ms + min(
        180,
        max(_FRAME_ROUNDING_GUARD_MS, (next_start_ms - after_ms) // 3),
    )
    return min(safe_end, max(safe_start, preferred))


def _safe_non_speech_regions(
    duration_ms: int,
    intervals: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return ranges whose frame-rounded cuts cannot land inside a spoken word."""
    regions: list[tuple[int, int]] = []
    for current, following in zip(intervals, intervals[1:]):
        safe_start = current[1] + _FRAME_ROUNDING_GUARD_MS
        safe_end = following[0] - _FRAME_ROUNDING_GUARD_MS
        if 0 < safe_start <= safe_end < duration_ms:
            regions.append((safe_start, safe_end))
    return regions


def _fallback_points_in_safe_regions(
    duration_ms: int,
    scene_count: int,
    regions: Sequence[tuple[int, int]],
    minimum_gap_ms: int,
) -> list[int]:
    """Build a bounded lattice of last-resort, but still word-safe, cut points.

    One point per word gap is not enough when an editor supplies many more images
    than sentences.  The lattice lets the global planner place several images in
    a genuinely silent interval without ever inventing a boundary inside speech.
    """
    points: set[int] = set()
    targets = [round(duration_ms * step / scene_count) for step in range(1, scene_count)]
    spacing_ms = max(1, minimum_gap_ms)
    for start_ms, end_ms in regions:
        if not 0 < start_ms <= end_ms < duration_ms:
            continue
        points.update((start_ms, end_ms))
        first_target = bisect_left(targets, start_ms)
        last_target = bisect_right(targets, end_ms)
        points.update(targets[first_target:last_target])

        point_ms = start_ms
        while point_ms <= end_ms:
            points.add(point_ms)
            point_ms += spacing_ms
        point_ms = end_ms
        while point_ms >= start_ms:
            points.add(point_ms)
            point_ms -= spacing_ms
    return sorted(points)


def _merge_candidates(
    candidates: list[BoundaryCandidate],
    merge_window_ms: int = _MAX_CANDIDATE_MERGE_MS,
) -> list[BoundaryCandidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: (item.time_ms, -_KIND_PRIORITY[item.kind]))
    groups: list[list[BoundaryCandidate]] = [[ordered[0]]]
    for candidate in ordered[1:]:
        if candidate.time_ms - groups[-1][0].time_ms <= merge_window_ms:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    merged: list[BoundaryCandidate] = []
    for group in groups:
        merged.append(max(group, key=lambda item: (_KIND_PRIORITY[item.kind], -item.time_ms)))
    return merged


def build_boundary_candidates(
    duration_ms: int,
    scene_count: int,
    pauses: Sequence[AudioPause],
    transcript: SpeechTranscript | None,
    *,
    minimum_gap_ms: int = 1,
) -> list[BoundaryCandidate]:
    candidates: list[BoundaryCandidate] = []
    ordered_pauses = sorted(pauses, key=lambda item: (item.start_ms, item.end_ms))
    pause_starts = [pause.start_ms for pause in ordered_pauses]
    intervals = _word_intervals(transcript) if transcript is not None else []
    interval_starts = [start_ms for start_ms, _end_ms in intervals]
    has_word_timing = transcript is not None and bool(intervals)

    for pause in ordered_pauses:
        cut_ms = _cut_inside_pause(pause)
        if (
            cut_ms is not None
            and pause.start_ms > 120
            and pause.end_ms < duration_ms - 120
            and 0 < cut_ms < duration_ms
            and (
                not has_word_timing
                or _has_word_guard(cut_ms, intervals, interval_starts)
            )
        ):
            kind: BoundaryKind = "long_pause" if pause.duration_ms >= 550 else "short_pause"
            candidates.append(BoundaryCandidate(cut_ms, kind))

    if has_word_timing:
        assert transcript is not None
        ordered_words = sorted(
            transcript.words,
            key=lambda item: (item.start_ms, item.end_ms),
        )
        # Последнее слово относится к последнему кадру: его окончание не должно
        # превращаться в переход на хвостовой тишине.
        for word in ordered_words[:-1]:
            if not ends_sentence(word.text) or not 0 < word.end_ms < duration_ms:
                continue
            sentence_end_ms = _move_out_of_word(
                word.end_ms,
                intervals,
                interval_starts,
            )
            next_start_ms = _next_speech_start(sentence_end_ms, interval_starts)
            safe_cut_ms = _safe_interword_cut(sentence_end_ms, next_start_ms)
            if safe_cut_ms is None:
                continue
            pause = _pause_after(
                sentence_end_ms,
                ordered_pauses,
                pause_starts,
                duration_ms,
            )
            if pause is not None:
                cut_ms = _cut_inside_pause(pause, sentence_end_ms)
                if (
                    cut_ms is not None
                    and _has_word_guard(cut_ms, intervals, interval_starts)
                ):
                    candidates.append(BoundaryCandidate(cut_ms, "sentence_pause"))
                else:
                    candidates.append(BoundaryCandidate(safe_cut_ms, "sentence_end"))
            else:
                candidates.append(BoundaryCandidate(safe_cut_ms, "sentence_end"))

        for segment in sorted(
            transcript.segments,
            key=lambda item: (item.start_ms, item.end_ms),
        ):
            if not 0 < segment.end_ms < duration_ms:
                continue
            segment_end_ms = _move_out_of_word(
                segment.end_ms,
                intervals,
                interval_starts,
            )
            # Позиция сегмента в массиве ненадёжна: провайдер может вернуть
            # неполный список. Граница внутренняя, только если после неё точно
            # распознано ещё хотя бы одно слово.
            next_start_ms = _next_speech_start(segment_end_ms, interval_starts)
            safe_cut_ms = _safe_interword_cut(segment_end_ms, next_start_ms)
            if safe_cut_ms is None:
                continue
            pause = _pause_after(
                segment_end_ms,
                ordered_pauses,
                pause_starts,
                duration_ms,
            )
            if pause is not None:
                cut_ms = _cut_inside_pause(pause, segment_end_ms)
                if (
                    cut_ms is not None
                    and _has_word_guard(cut_ms, intervals, interval_starts)
                ):
                    candidates.append(BoundaryCandidate(cut_ms, "segment_pause"))
                else:
                    candidates.append(BoundaryCandidate(safe_cut_ms, "segment_end"))
            else:
                candidates.append(BoundaryCandidate(safe_cut_ms, "segment_end"))

        for current, following in zip(intervals, intervals[1:]):
            word_boundary = _safe_interword_cut(current[1], following[0])
            if word_boundary is not None and 0 < word_boundary < duration_ms:
                candidates.append(BoundaryCandidate(word_boundary, "word_boundary"))

        safe_regions = _safe_non_speech_regions(duration_ms, intervals)
        for point_ms in _fallback_points_in_safe_regions(
            duration_ms,
            scene_count,
            safe_regions,
            minimum_gap_ms,
        ):
            candidates.append(BoundaryCandidate(point_ms, "fallback"))
    else:
        for index in range(1, scene_count):
            target = round(duration_ms * index / scene_count)
            candidates.append(BoundaryCandidate(target, "fallback"))
    merge_window_ms = min(
        _MAX_CANDIDATE_MERGE_MS,
        max(0, minimum_gap_ms // 3),
    )
    return _merge_candidates(candidates, merge_window_ms)


def _valid_exact_sentence_plan(
    candidates: Sequence[BoundaryCandidate],
    required: int,
    duration_ms: int,
    minimum_gap_ms: int,
) -> list[BoundaryDecision] | None:
    sentence_candidates = [
        candidate
        for candidate in candidates
        if candidate.kind in {"sentence_pause", "sentence_end"}
    ]
    if len(sentence_candidates) != required:
        return None
    points = [0, *(candidate.time_ms for candidate in sentence_candidates), duration_ms]
    if all(end - start >= minimum_gap_ms for start, end in zip(points, points[1:])):
        return [BoundaryDecision(item.time_ms, item.kind) for item in sentence_candidates]
    return None


def _speech_progress_by_candidate(
    candidates: Sequence[BoundaryCandidate],
    transcript: SpeechTranscript | None,
) -> list[float] | None:
    if transcript is None:
        return None
    words = sorted(
        (word for word in transcript.words if word.end_ms > word.start_ms),
        key=lambda word: (word.end_ms, word.start_ms),
    )
    if not words:
        return None

    word_ends = [word.end_ms for word in words]
    cumulative_spoken_ms: list[int] = [0]
    for word in words:
        cumulative_spoken_ms.append(
            cumulative_spoken_ms[-1] + word.end_ms - word.start_ms
        )
    total_spoken_ms = max(1, cumulative_spoken_ms[-1])

    ordered_for_sentences = sorted(
        words,
        key=lambda word: (word.start_ms, word.end_ms),
    )
    internal_sentence_ends = sorted(
        word.end_ms for word in ordered_for_sentences[:-1] if ends_sentence(word.text)
    )
    sentence_count = max(1, len(internal_sentence_ends) + 1)

    progress: list[float] = []
    for candidate in candidates:
        completed_words = bisect_right(word_ends, candidate.time_ms)
        word_progress = completed_words / len(words)
        spoken_progress = cumulative_spoken_ms[completed_words] / total_spoken_ms
        completed_sentences = bisect_right(internal_sentence_ends, candidate.time_ms)
        sentence_progress = completed_sentences / sentence_count
        progress.append(
            0.55 * word_progress
            + 0.25 * spoken_progress
            + 0.20 * sentence_progress
        )
    return progress


def _greedy_path_indexes(
    candidates: Sequence[BoundaryCandidate],
    duration_ms: int,
    scene_count: int,
    minimum_gap_ms: int,
) -> list[int] | None:
    """Return an earliest feasible path, or None when no such path exists.

    Selecting the earliest eligible point leaves at least as much room for every
    following boundary as any alternative selection, so the check is exact for a
    fixed minimum gap and does not need dynamic programming.
    """
    required = scene_count - 1
    chosen: list[int] = []
    previous_time_ms = 0
    latest_boundary_ms = duration_ms - minimum_gap_ms
    for index, candidate in enumerate(candidates):
        if candidate.time_ms - previous_time_ms < minimum_gap_ms:
            continue
        if candidate.time_ms > latest_boundary_ms:
            break
        chosen.append(index)
        previous_time_ms = candidate.time_ms
        if len(chosen) == required:
            return chosen
    return None


def _maximum_feasible_minimum(
    candidates: Sequence[BoundaryCandidate],
    duration_ms: int,
    scene_count: int,
    minimum_gap_floor_ms: int,
    preferred_gap_ms: int,
) -> tuple[int, list[int]] | None:
    low_ms = minimum_gap_floor_ms
    high_ms = preferred_gap_ms
    best_gap_ms = 0
    best_path: list[int] | None = None
    while low_ms <= high_ms:
        candidate_gap_ms = (low_ms + high_ms) // 2
        path = _greedy_path_indexes(
            candidates,
            duration_ms,
            scene_count,
            candidate_gap_ms,
        )
        if path is None:
            high_ms = candidate_gap_ms - 1
        else:
            best_gap_ms = candidate_gap_ms
            best_path = path
            low_ms = candidate_gap_ms + 1
    if best_path is None:
        return None
    return best_gap_ms, best_path


def _prune_planning_candidates(
    candidates: Sequence[BoundaryCandidate],
    speech_progress: Sequence[float] | None,
    duration_ms: int,
    scene_count: int,
    mandatory_indexes: Sequence[int],
) -> tuple[list[BoundaryCandidate], list[float] | None]:
    """Bound the expensive scoring DP while retaining a proven feasible path."""
    maximum_count = max(
        256,
        scene_count * _MAX_PLANNING_CANDIDATES_PER_SCENE,
    )
    if len(candidates) <= maximum_count:
        copied_progress = list(speech_progress) if speech_progress is not None else None
        return list(candidates), copied_progress

    keep = set(mandatory_indexes)
    times = [candidate.time_ms for candidate in candidates]

    def retain_neighborhood(insertion: int) -> None:
        for index in range(max(0, insertion - 1), min(len(candidates), insertion + 2)):
            keep.add(index)

    for step in range(1, scene_count):
        target_time_ms = round(duration_ms * step / scene_count)
        retain_neighborhood(bisect_left(times, target_time_ms))
        if speech_progress is not None:
            retain_neighborhood(bisect_left(speech_progress, step / scene_count))

    bucket_count = min(len(candidates), max(1, scene_count * 2))
    bucket_winners: dict[int, int] = {}
    for index, candidate in enumerate(candidates):
        bucket = min(
            bucket_count - 1,
            candidate.time_ms * bucket_count // duration_ms,
        )
        center_ms = (bucket + 0.5) * duration_ms / bucket_count
        previous = bucket_winners.get(bucket)
        if previous is None:
            bucket_winners[bucket] = index
            continue
        previous_candidate = candidates[previous]
        if (
            _KIND_PRIORITY[candidate.kind],
            -abs(candidate.time_ms - center_ms),
        ) > (
            _KIND_PRIORITY[previous_candidate.kind],
            -abs(previous_candidate.time_ms - center_ms),
        ):
            bucket_winners[bucket] = index
    keep.update(bucket_winners.values())

    selected_indexes = sorted(keep)
    selected_candidates = [candidates[index] for index in selected_indexes]
    selected_progress = (
        [speech_progress[index] for index in selected_indexes]
        if speech_progress is not None
        else None
    )
    return selected_candidates, selected_progress


def _candidate_cost(
    candidate: BoundaryCandidate,
    candidate_index: int,
    step: int,
    scene_count: int,
    duration_ms: int,
    strategy: SyncStrategy,
    speech_progress: Sequence[float] | None,
) -> float:
    target_progress = step / scene_count
    time_deviation = (
        candidate.time_ms / duration_ms - target_progress
    ) * scene_count
    time_weight, speech_weight = _STRATEGY_BALANCE_WEIGHTS[strategy]
    cost = (
        _KIND_PENALTY[candidate.kind]
        * _STRATEGY_KIND_MULTIPLIER[strategy]
        + time_weight * time_deviation * time_deviation
    )
    if speech_progress is not None:
        speech_deviation = (
            speech_progress[candidate_index] - target_progress
        ) * scene_count
        cost += speech_weight * speech_deviation * speech_deviation
    return cost


def _plan_with_minimum_gap(
    candidates: Sequence[BoundaryCandidate],
    duration_ms: int,
    scene_count: int,
    minimum_gap_ms: int,
    strategy: SyncStrategy,
    speech_progress: Sequence[float] | None,
) -> list[BoundaryDecision] | None:
    required = scene_count - 1
    count = len(candidates)
    previous_costs = [inf] * count
    parent_rows: list[array] = []

    for step in range(1, required + 1):
        maximum_time = duration_ms - (scene_count - step) * minimum_gap_ms
        current_costs = [inf] * count
        parents = array("i", [-1]) * count
        best_previous_cost = inf
        best_previous_index = -1
        eligible_index = 0

        for index, candidate in enumerate(candidates):
            if step > 1:
                while (
                    eligible_index < index
                    and candidates[eligible_index].time_ms
                    <= candidate.time_ms - minimum_gap_ms
                ):
                    previous_cost = previous_costs[eligible_index]
                    if previous_cost < best_previous_cost:
                        best_previous_cost = previous_cost
                        best_previous_index = eligible_index
                    eligible_index += 1

            if candidate.time_ms < step * minimum_gap_ms or candidate.time_ms > maximum_time:
                continue
            local_cost = _candidate_cost(
                candidate,
                index,
                step,
                scene_count,
                duration_ms,
                strategy,
                speech_progress,
            )
            if step == 1:
                current_costs[index] = local_cost
            elif best_previous_index >= 0:
                current_costs[index] = best_previous_cost + local_cost
                parents[index] = best_previous_index

        previous_costs = current_costs
        parent_rows.append(parents)

    final_index = min(
        range(count),
        key=lambda index: (previous_costs[index], candidates[index].time_ms),
    )
    if previous_costs[final_index] == inf:
        return None

    chosen_indexes = [final_index]
    for row in reversed(parent_rows[1:]):
        final_index = row[final_index]
        if final_index < 0:
            return None
        chosen_indexes.append(final_index)
    chosen_indexes.reverse()
    return [
        BoundaryDecision(candidates[index].time_ms, candidates[index].kind)
        for index in chosen_indexes
    ]


def plan_scene_boundaries(
    duration_ms: int,
    scene_count: int,
    pauses: Sequence[AudioPause],
    transcript: SpeechTranscript | None = None,
    *,
    preferred_minimum_scene_ms: int = 700,
    strategy: SyncStrategy = "adaptive",
) -> list[BoundaryDecision]:
    if scene_count <= 1:
        return []
    if duration_ms <= 0:
        raise ValueError("Длительность аудио должна быть больше нуля.")
    if strategy not in _STRATEGY_BALANCE_WEIGHTS:
        raise ValueError(f"Неизвестная стратегия синхронизации: {strategy}.")
    required = scene_count - 1
    preferred_gap_ms = min(
        max(1, preferred_minimum_scene_ms),
        max(1, duration_ms // scene_count),
    )
    candidate_spacing_ms = min(
        preferred_gap_ms,
        max(1, duration_ms // (scene_count * 3)),
    )
    candidates = build_boundary_candidates(
        duration_ms,
        scene_count,
        pauses,
        transcript,
        minimum_gap_ms=candidate_spacing_ms,
    )
    if strategy in {"adaptive", "semantic"}:
        exact = _valid_exact_sentence_plan(
            candidates,
            required,
            duration_ms,
            preferred_gap_ms,
        )
        if exact is not None:
            return exact

    count = len(candidates)
    if count < required:
        raise ValueError(
            "Недостаточно безопасных пауз и промежутков между словами для выбранного "
            "количества кадров. Уменьшите число кадров или используйте другую озвучку."
        )

    feasible = _maximum_feasible_minimum(
        candidates,
        duration_ms,
        scene_count,
        candidate_spacing_ms,
        preferred_gap_ms,
    )
    if feasible is not None:
        maximum_gap_ms, mandatory_path = feasible
        speech_progress = _speech_progress_by_candidate(candidates, transcript)
        planning_candidates, planning_progress = _prune_planning_candidates(
            candidates,
            speech_progress,
            duration_ms,
            scene_count,
            mandatory_path,
        )
        plan = _plan_with_minimum_gap(
            planning_candidates,
            duration_ms,
            scene_count,
            maximum_gap_ms,
            strategy,
            planning_progress,
        )
        if plan is not None:
            return plan

    raise ValueError(
        "Недостаточно безопасных границ с подходящим расстоянием для непрерывной "
        "синхронизации. Уменьшите число кадров или минимальную длительность сцены."
    )
