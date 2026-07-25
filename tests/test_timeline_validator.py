from __future__ import annotations

from pathlib import Path

from models.timeline import TimelineItem
from services.timeline_validator import validate_timeline


def item(start: int, end: int, name: str = "x.jpg") -> TimelineItem:
    return TimelineItem(1, name, Path(name), "[0-01]", start, end, end - start)


def test_empty_is_invalid() -> None:
    assert validate_timeline([])[0].critical


def test_decreasing_or_zero_duration_is_invalid() -> None:
    issues = validate_timeline([item(0, 1_000, "a.jpg"), item(1_000, 1_000, "b.jpg")])
    assert any("нулевую или отрицательную" in issue.message for issue in issues)


def test_gap_is_invalid() -> None:
    issues = validate_timeline([item(100, 1_000)])
    assert any("Нарушена непрерывность" in issue.message for issue in issues)


def test_decreasing_time_is_invalid() -> None:
    issues = validate_timeline([item(0, 1_000, "a.jpg"), item(1_000, 900, "b.jpg")])
    assert any("нулевую или отрицательную" in issue.message for issue in issues)
