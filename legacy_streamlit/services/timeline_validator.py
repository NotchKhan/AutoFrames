from __future__ import annotations

from collections.abc import Sequence

from models.timeline import TimelineItem, ValidationIssue
from utils.time_utils import format_ms
from utils.time_utils import frame_index


def validate_timeline(items: Sequence[TimelineItem]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not items:
        return [ValidationIssue("Не добавлено ни одного корректного изображения.")]
    previous_end = 0
    seen: dict[int, str] = {}
    for item in items:
        if not item.is_valid or item.errors:
            issues.append(ValidationIssue(
                f"Кадр «{item.original_filename}» отмечен как некорректный. "
                "Исправьте ошибки файла перед рендерингом."
            ))
        if item.end_ms in seen:
            issues.append(ValidationIssue(
                f"Файлы «{seen[item.end_ms]}» и «{item.original_filename}» имеют одинаковое "
                f"время окончания {format_ms(item.end_ms)}."
            ))
        seen[item.end_ms] = item.original_filename
        if item.start_ms != previous_end:
            issues.append(ValidationIssue(
                f"Нарушена непрерывность перед файлом «{item.original_filename}»: начало "
                f"{format_ms(item.start_ms)}, ожидалось {format_ms(previous_end)}."
            ))
        if item.end_ms <= item.start_ms or item.duration_ms <= 0:
            issues.append(ValidationIssue(
                f"Кадр «{item.original_filename}» имеет нулевую или отрицательную длительность."
            ))
        previous_end = item.end_ms
    return issues


def validate_timeline_for_fps(
    items: Sequence[TimelineItem], fps: int
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    previous_frame = 0
    for item in items:
        end_frame = frame_index(item.end_ms, fps)
        if end_frame <= previous_frame:
            issues.append(ValidationIssue(
                f"Кадр «{item.original_filename}» короче одного физического кадра при {fps} FPS. "
                "Увеличьте его время окончания или выберите более высокий FPS."
            ))
        previous_frame = end_frame
    return issues
