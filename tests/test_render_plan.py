from __future__ import annotations

from pathlib import Path

import pytest

from models.render import AudioSettings, RenderSettings, VideoSettings
from models.timeline import TimelineItem
from services.video_renderer import build_render_plan


def items() -> list[TimelineItem]:
    return [
        TimelineItem(1, "a.jpg", Path("a.jpg"), "[0-05]", 0, 5_000, 5_000),
        TimelineItem(2, "b.jpg", Path("b.jpg"), "[0-10]", 5_000, 10_000, 5_000),
    ]


def settings(mode: str, start: int = 0, end: int | None = None) -> RenderSettings:
    return RenderSettings(VideoSettings(), AudioSettings(), mode, preview_start_ms=start, preview_end_ms=end)  # type: ignore[arg-type]


def test_extend_last_to_audio() -> None:
    segments, offset, duration, pad = build_render_plan(items(), 12_000, settings("extend_last"))
    assert segments[-1].end_ms == 12_000
    assert (offset, duration, pad) == (0, 12_000, False)


def test_black_tail() -> None:
    segments, _, duration, _ = build_render_plan(items(), 12_000, settings("black"))
    assert segments[-1].source_path is None
    assert duration == 12_000


def test_trim_video_and_preview_range() -> None:
    segments, offset, duration, _ = build_render_plan(
        items(), 8_000, settings("trim_video", 2_000, 7_000)
    )
    assert offset == 2_000
    assert duration == 5_000
    assert segments[0].start_ms == 0
    assert segments[-1].end_ms == 5_000


def test_error_mode_rejects_long_video() -> None:
    with pytest.raises(ValueError, match="Исправьте таймлайн"):
        build_render_plan(items(), 8_000, settings("error"))
