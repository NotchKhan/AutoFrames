from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from config import MIN_FREE_RESERVE_BYTES
from models.render import VideoSettings
from models.timeline import TimelineItem


@dataclass(frozen=True, slots=True)
class DiskEstimate:
    required_bytes: int
    free_bytes: int
    reserve_bytes: int

    @property
    def sufficient(self) -> bool:
        return self.free_bytes >= self.required_bytes


def estimate_required_disk_bytes(
    items: list[TimelineItem],
    settings: VideoSettings,
    duration_ms: int,
) -> int:
    unique_sources = len({item.stored_path for item in items})
    prepared_frames = unique_sources * settings.width * settings.height * 3
    seconds = max(duration_ms, 1) / 1000
    quality_factor = max(0.6, (28 - settings.crf) / 8)
    motion_factor = 1.35 if settings.motion_mode != "none" else 1.0
    estimated_video_bitrate = (
        settings.width * settings.height * settings.fps * 0.08 * quality_factor * motion_factor
    )
    encoded_passes = int(estimated_video_bitrate * seconds / 8 * 3.2)
    audio_and_overhead = int(seconds * 32_000 + 64 * 1024 * 1024)
    return prepared_frames + encoded_passes + audio_and_overhead + MIN_FREE_RESERVE_BYTES


def disk_estimate(
    directory: Path,
    items: list[TimelineItem],
    settings: VideoSettings,
    duration_ms: int,
) -> DiskEstimate:
    free = shutil.disk_usage(directory).free
    required = estimate_required_disk_bytes(items, settings, duration_ms)
    return DiskEstimate(required, free, MIN_FREE_RESERVE_BYTES)
