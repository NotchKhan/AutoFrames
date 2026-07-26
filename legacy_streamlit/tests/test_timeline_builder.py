from __future__ import annotations

from pathlib import Path
import random

import pytest

from models.timeline import SourceImage
from services.timeline_builder import build_timeline


def src(name: str) -> SourceImage:
    return SourceImage(name, Path(name))


def test_numeric_sort_and_continuous_timeline() -> None:
    items, issues = build_timeline([
        src("[10-00]_d.jpg"), src("[2-05]_c.jpg"),
        src("[0-59]_a.jpg"), src("[1-02]_b.jpg"),
    ])
    assert not issues
    assert [item.end_ms for item in items] == [59_000, 62_000, 125_000, 600_000]
    assert [item.start_ms for item in items] == [0, 59_000, 62_000, 125_000]
    assert [item.duration_ms for item in items] == [59_000, 3_000, 63_000, 475_000]


def test_duplicate_end_is_error() -> None:
    items, issues = build_timeline([src("[0-15]_один.jpg"), src("[0-15]_два.PNG")])
    assert len(items) == 2
    assert any("одинаковое время" in issue.message for issue in issues)
    assert all(not item.is_valid for item in items)


def test_manual_override_rebuilds_order() -> None:
    sources = [src("[0-05]_a.jpg"), src("[0-10]_b.jpg")]
    items, issues = build_timeline(sources, {"[0-05]_a.jpg": 15_000})
    assert not issues
    assert [item.original_filename for item in items] == ["[0-10]_b.jpg", "[0-05]_a.jpg"]
    assert items[1].manually_overridden


@pytest.mark.parametrize("count", [1, 10, 150])
def test_projects_of_different_sizes_in_random_order(count: int) -> None:
    sources = [src(f"[{index // 60}-{index % 60:02d}]_кадр {index}.jpg") for index in range(1, count + 1)]
    random.Random(42).shuffle(sources)
    items, issues = build_timeline(sources)
    assert not issues
    assert len(items) == count
    assert items[0].start_ms == 0
    assert all(current.start_ms == previous.end_ms for previous, current in zip(items, items[1:]))


def test_very_long_cyrillic_filename() -> None:
    name = "[0-05]_" + "очень длинное имя " * 40 + ".JPEG"
    items, issues = build_timeline([src(name)])
    assert not issues
    assert items[0].end_ms == 5_000
