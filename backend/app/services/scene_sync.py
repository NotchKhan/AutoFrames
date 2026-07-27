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


_KIND_PENALTY: dict[BoundaryKind, float] = {
    "sentence_pause": 0.0,
    "sentence_end": 0.4,
    "segment_pause": 1.4,
    "long_pause": 2.0,
    "segment_end": 2.8,
    "short_pause": 3.5,
    "word_boundary": 7.0,
    "fallback": 20.0,
}
_KIND_PRIORITY = {kind: -penalty for kind, penalty in _KIND_PENALTY.items()}
# При 24 FPS округление к ближайшему кадру может сдвинуть границу на 20,84 мс.
# Запас 22 мс сохраняет смену между словами при любом поддерживаемом FPS.
_FRAME_ROUNDING_GUARD_MS = 22


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


def _merge_candidates(candidates: list[BoundaryCandidate]) -> list[BoundaryCandidate]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: (item.time_ms, -_KIND_PRIORITY[item.kind]))
    groups: list[list[BoundaryCandidate]] = [[ordered[0]]]
    for candidate in ordered[1:]:
        if candidate.time_ms - groups[-1][-1].time_ms <= 90:
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
) -> list[BoundaryCandidate]:
    candidates: list[BoundaryCandidate] = []
    ordered_pauses = sorted(pauses, key=lambda item: (item.start_ms, item.end_ms))
    pause_starts = [pause.start_ms for pause in ordered_pauses]
    intervals = _word_intervals(transcript) if transcript is not None else []
    interval_starts = [start_ms for start_ms, _end_ms in intervals]

    for pause in ordered_pauses:
        cut_ms = _cut_inside_pause(pause)
        if (
            cut_ms is not None
            and pause.start_ms > 120
            and pause.end_ms < duration_ms - 120
            and 0 < cut_ms < duration_ms
            and (
                transcript is None
                or _has_word_guard(cut_ms, intervals, interval_starts)
            )
        ):
            kind: BoundaryKind = "long_pause" if pause.duration_ms >= 550 else "short_pause"
            candidates.append(BoundaryCandidate(cut_ms, kind))

    if transcript is not None:
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
    else:
        for index in range(1, scene_count):
            target = round(duration_ms * index / scene_count)
            candidates.append(BoundaryCandidate(target, "fallback"))
    return _merge_candidates(candidates)


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


def plan_scene_boundaries(
    duration_ms: int,
    scene_count: int,
    pauses: Sequence[AudioPause],
    transcript: SpeechTranscript | None = None,
    *,
    preferred_minimum_scene_ms: int = 700,
) -> list[BoundaryDecision]:
    if scene_count <= 1:
        return []
    required = scene_count - 1
    minimum_gap_ms = min(
        preferred_minimum_scene_ms,
        max(1, duration_ms // (scene_count * 3)),
    )
    candidates = build_boundary_candidates(duration_ms, scene_count, pauses, transcript)
    exact = _valid_exact_sentence_plan(
        candidates,
        required,
        duration_ms,
        minimum_gap_ms,
    )
    if exact is not None:
        return exact

    count = len(candidates)
    if count < required:
        raise ValueError(
            "Недостаточно безопасных пауз и промежутков между словами для выбранного "
            "количества кадров. Уменьшите число кадров или используйте другую озвучку."
        )
    previous_costs = [inf] * count
    parent_rows: list[array] = []
    average_scene_ms = max(1.0, duration_ms / scene_count)

    for step in range(1, required + 1):
        target = duration_ms * step / scene_count
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
                    if previous_costs[eligible_index] < best_previous_cost:
                        best_previous_cost = previous_costs[eligible_index]
                        best_previous_index = eligible_index
                    eligible_index += 1

            if candidate.time_ms < step * minimum_gap_ms or candidate.time_ms > maximum_time:
                continue
            pacing_cost = abs(candidate.time_ms - target) / average_scene_ms
            local_cost = _KIND_PENALTY[candidate.kind] + pacing_cost
            if step == 1:
                current_costs[index] = local_cost
            elif best_previous_index >= 0:
                current_costs[index] = best_previous_cost + local_cost
                parents[index] = best_previous_index

        previous_costs = current_costs
        parent_rows.append(parents)

    final_index = min(range(count), key=lambda index: previous_costs[index])
    if previous_costs[final_index] == inf:
        raise ValueError(
            "Безопасные границы расположены слишком близко друг к другу для непрерывной "
            "синхронизации. Уменьшите число кадров или минимальную длительность сцены."
        )

    chosen_indexes = [final_index]
    for row in reversed(parent_rows[1:]):
        final_index = row[final_index]
        if final_index < 0:
            raise ValueError("Повреждён план синхронизации сцен.")
        chosen_indexes.append(final_index)
    chosen_indexes.reverse()
    return [
        BoundaryDecision(candidates[index].time_ms, candidates[index].kind)
        for index in chosen_indexes
    ]
