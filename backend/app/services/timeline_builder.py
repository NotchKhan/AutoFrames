from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from models.timeline import SourceImage, TimelineItem, ValidationIssue
from services.audio_analyzer import AudioPause
from services.filename_parser import TimestampParseError, parse_timestamp
from services.scene_sync import BoundaryKind, SyncStrategy, plan_scene_boundaries
from services.speech_recognizer import SpeechTranscript
from utils.time_utils import format_ms, frame_index


_MAX_SUPPORTED_FPS = 60


class MixedTimelineModeError(ValueError):
    """Оставлено для совместимости старых импортов; режим имён больше не используется."""


def timeline_mode_for_filenames(sources: Sequence[SourceImage]) -> str:
    del sources
    return "audio_pauses"


def filenames_have_timestamps(sources: Sequence[SourceImage]) -> bool:
    del sources
    return False


_BOUNDARY_LABELS: dict[BoundaryKind, str] = {
    "sentence_pause": "конец предложения · пауза",
    "sentence_end": "конец предложения",
    "segment_pause": "конец фразы · пауза",
    "segment_end": "конец фразы",
    "long_pause": "длинная пауза",
    "short_pause": "пауза",
    "word_boundary": "после слова",
    "fallback": "безопасная резервная точка",
}


def build_audio_timeline(
    sources: Sequence[SourceImage],
    audio_duration_ms: int,
    pauses: Sequence[AudioPause],
    transcript: SpeechTranscript | None = None,
    *,
    preferred_minimum_scene_ms: int = 700,
    strategy: SyncStrategy = "adaptive",
) -> tuple[list[TimelineItem], list[ValidationIssue]]:
    if not sources:
        return [], [ValidationIssue("Не добавлено ни одного изображения.")]
    if audio_duration_ms <= 0:
        return [], [ValidationIssue("Длительность аудио должна быть больше нуля.")]
    available_frames = frame_index(audio_duration_ms, _MAX_SUPPORTED_FPS)
    if available_frames < len(sources):
        return [], [ValidationIssue(
            f"Для {len(sources)} изображений аудиодорожка слишком короткая: даже при "
            f"{_MAX_SUPPORTED_FPS} FPS в ней только {available_frames} физических кадров. "
            "Уменьшите число изображений или используйте более длинную озвучку."
        )]

    # Имя файла — только подпись для пользователя. Порядок всегда совпадает
    # с порядком добавления, включая загрузку несколькими последовательными пакетами.
    ordered = list(sources)
    try:
        detected = plan_scene_boundaries(
            audio_duration_ms,
            len(ordered),
            pauses,
            transcript,
            preferred_minimum_scene_ms=preferred_minimum_scene_ms,
            strategy=strategy,
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
            if transcript is not None and transcript.words:
                warnings.append(
                    "Не найдено смысловой границы; использована резервная точка вне слов."
                )
            else:
                warnings.append(
                    "Нет временных меток слов; кадр распределён равномерно."
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
        if boundary_kind == "audio_end":
            parsed_timestamp = "конец аудио"
        elif boundary_kind == "fallback" and not (
            transcript is not None and transcript.words
        ):
            parsed_timestamp = "равномерно"
        else:
            parsed_timestamp = _BOUNDARY_LABELS[boundary_kind]
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

    previous_frame = 0
    for item in items:
        end_frame = frame_index(item.end_ms, _MAX_SUPPORTED_FPS)
        if end_frame <= previous_frame:
            return items, [ValidationIssue(
                f"Кадр «{item.original_filename}» короче одного физического кадра даже при "
                f"{_MAX_SUPPORTED_FPS} FPS. Уменьшите число изображений или используйте "
                "более длинную озвучку."
            )]
        previous_frame = end_frame
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
