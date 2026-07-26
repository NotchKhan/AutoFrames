from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from models.timeline import SourceImage, TimelineItem, ValidationIssue
from services.filename_parser import TimestampParseError, parse_timestamp
from utils.time_utils import format_ms


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
