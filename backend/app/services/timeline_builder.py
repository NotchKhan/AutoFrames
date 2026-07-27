from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from models.timeline import SourceImage, TimelineItem, ValidationIssue
from services.audio_analyzer import AudioPause
from services.filename_parser import TimestampParseError, parse_timestamp
from services.scene_sync import BoundaryKind, plan_scene_boundaries
from services.speech_recognizer import SpeechTranscript
from utils.time_utils import format_ms


_NATURAL_PART_RE = re.compile(r"(\d+)")


class MixedTimelineModeError(ValueError):
    """В одном наборе нельзя смешивать ручные метки и автоматические имена."""


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in _NATURAL_PART_RE.split(value)
        if part
    )


def timeline_mode_for_filenames(sources: Sequence[SourceImage]) -> str:
    if not sources:
        return "audio_pauses"
    timestamped = 0
    plain = 0
    for source in sources:
        try:
            parse_timestamp(source.original_filename)
            timestamped += 1
        except TimestampParseError:
            if source.original_filename.startswith("["):
                raise
            plain += 1
    if timestamped and plain:
        raise MixedTimelineModeError(
            "Нельзя смешивать изображения с ручными метками [MM-SS] и обычными именами. "
            "Используйте один режим для всего проекта."
        )
    return "timestamps" if timestamped else "audio_pauses"


def filenames_have_timestamps(sources: Sequence[SourceImage]) -> bool:
    try:
        return timeline_mode_for_filenames(sources) == "timestamps"
    except (MixedTimelineModeError, TimestampParseError):
        return False


_BOUNDARY_LABELS: dict[BoundaryKind, str] = {
    "sentence_pause": "конец предложения · пауза",
    "sentence_end": "конец предложения",
    "segment_pause": "конец фразы · пауза",
    "segment_end": "конец фразы",
    "long_pause": "длинная пауза",
    "short_pause": "пауза",
    "word_boundary": "после слова",
    "fallback": "равномерно",
}


def build_audio_timeline(
    sources: Sequence[SourceImage],
    audio_duration_ms: int,
    pauses: Sequence[AudioPause],
    transcript: SpeechTranscript | None = None,
    *,
    preferred_minimum_scene_ms: int = 700,
) -> tuple[list[TimelineItem], list[ValidationIssue]]:
    if not sources:
        return [], [ValidationIssue("Не добавлено ни одного изображения.")]
    if audio_duration_ms <= 0:
        return [], [ValidationIssue("Длительность аудио должна быть больше нуля.")]
    if audio_duration_ms < len(sources):
        return [], [ValidationIssue(
            "Аудиодорожка слишком короткая для выбранного количества изображений."
        )]

    ordered = sorted(
        sources,
        key=lambda source: (
            _natural_key(source.original_filename),
            source.original_filename.casefold(),
            source.original_filename,
        ),
    )
    try:
        detected = plan_scene_boundaries(
            audio_duration_ms,
            len(ordered),
            pauses,
            transcript,
            preferred_minimum_scene_ms=preferred_minimum_scene_ms,
        )
    except ValueError as exc:
        return [], [ValidationIssue(str(exc))]
    ends = [decision.time_ms for decision in detected] + [audio_duration_ms]
    kinds: list[BoundaryKind | Literal["audio_end"]] = [
        *(decision.kind for decision in detected),
        "audio_end",
    ]
    items: list[TimelineItem] = []
    previous_end = 0
    average_scene_ms = audio_duration_ms / len(ordered)

    for index, (source, end_ms, boundary_kind) in enumerate(
        zip(ordered, ends, kinds, strict=True),
        start=1,
    ):
        warnings: list[str] = []
        if boundary_kind == "fallback":
            warnings.append(
                "Не найдено безопасной речевой границы; кадр распределён равномерно."
            )
        elif boundary_kind == "word_boundary":
            warnings.append("Подходящая пауза не найдена; смена поставлена после ближайшего слова.")
        if end_ms - previous_end < preferred_minimum_scene_ms:
            warnings.append(
                f"Сцена короче рекомендуемых {preferred_minimum_scene_ms / 1000:g} с."
            )
        if len(ordered) > 1 and end_ms - previous_end > average_scene_ms * 2.4:
            warnings.append(
                "Сцена значительно длиннее средней: проверьте соответствие числа кадров тексту."
            )
        parsed_timestamp = (
            "конец аудио"
            if boundary_kind == "audio_end"
            else _BOUNDARY_LABELS[boundary_kind]
        )
        items.append(TimelineItem(
            index=index,
            original_filename=source.original_filename,
            stored_path=source.stored_path,
            parsed_timestamp=parsed_timestamp,
            start_ms=previous_end,
            end_ms=end_ms,
            duration_ms=end_ms - previous_end,
            warnings=warnings,
            boundary_kind=boundary_kind,
        ))
        previous_end = end_ms
    return items, []


def build_timeline(
    sources: Sequence[SourceImage],
    overrides_ms: Mapping[str, int] | None = None,
) -> tuple[list[TimelineItem], list[ValidationIssue]]:
    overrides = overrides_ms or {}
    parsed: list[tuple[SourceImage, str, int, bool]] = []
    issues: list[ValidationIssue] = []

    for source in sources:
        try:
            timestamp = parse_timestamp(source.original_filename)
        except TimestampParseError as exc:
            issues.append(ValidationIssue(str(exc), source.original_filename))
            continue
        source_key = str(source.stored_path)
        safe_key = source.stored_path.name
        overridden = (
            safe_key in overrides
            or source_key in overrides
            or source.original_filename in overrides
        )
        end_ms = overrides.get(
            safe_key,
            overrides.get(source_key, overrides.get(source.original_filename, timestamp.milliseconds)),
        )
        if end_ms <= 0:
            issues.append(ValidationIssue(
                f"Файл «{source.original_filename}» заканчивается в {format_ms(end_ms)}. "
                "Время окончания первого и любого другого кадра должно быть больше нуля.",
                source.original_filename,
            ))
            continue
        parsed.append((source, timestamp.raw, end_ms, overridden))

    parsed.sort(key=lambda row: (row[2], row[0].original_filename.casefold()))
    duplicates: dict[int, list[str]] = defaultdict(list)
    for source, _, end_ms, _ in parsed:
        duplicates[end_ms].append(source.original_filename)
    for end_ms, names in duplicates.items():
        if len(names) > 1:
            quoted = " и ".join(f"«{name}»" for name in names)
            issues.append(ValidationIssue(
                f"Файлы {quoted} имеют одинаковое время окончания {format_ms(end_ms)}. "
                "Каждый кадр должен иметь уникальное время окончания."
            ))

    items: list[TimelineItem] = []
    previous_end = 0
    for index, (source, raw, end_ms, overridden) in enumerate(parsed, start=1):
        duration = end_ms - previous_end
        item_errors: list[str] = []
        if duration <= 0:
            item_errors.append(
                f"Кадр «{source.original_filename}» имеет нулевую или отрицательную длительность."
            )
        items.append(TimelineItem(
            index=index,
            original_filename=source.original_filename,
            stored_path=source.stored_path,
            parsed_timestamp=raw,
            start_ms=previous_end,
            end_ms=end_ms,
            duration_ms=duration,
            is_valid=not item_errors and len(duplicates[end_ms]) == 1,
            errors=item_errors,
            manually_overridden=overridden,
        ))
        previous_end = end_ms
    return items, issues
