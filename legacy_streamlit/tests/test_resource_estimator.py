from __future__ import annotations

from pathlib import Path

from models.render import VideoSettings
from models.timeline import TimelineItem
from services.resource_estimator import estimate_required_disk_bytes


def test_disk_estimate_grows_with_resolution_and_duration() -> None:
    items = [TimelineItem(1, "a.jpg", Path("a.jpg"), "[0-10]", 0, 10_000, 10_000)]
    small = estimate_required_disk_bytes(items, VideoSettings(width=320, height=240), 10_000)
    large = estimate_required_disk_bytes(items, VideoSettings(width=1920, height=1080), 60_000)
    assert large > small > 0

