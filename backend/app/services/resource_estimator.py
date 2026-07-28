from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from config import (
    STORAGE_RESERVE_MAX_BYTES,
    STORAGE_RESERVE_MIN_BYTES,
    STORAGE_RESERVE_PERCENT,
)
from models.render import VideoSettings
from models.timeline import TimelineItem


ENCODED_TRANSIENT_MULTIPLIER = 4.5


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
    *,
    reserve_bytes: int = STORAGE_RESERVE_MIN_BYTES,
) -> int:
    if reserve_bytes < 0:
        raise ValueError("Резерв свободного места не может быть отрицательным.")
    prepared_frame_factor = (
        2
        if settings.motion_mode == "smart" and settings.scale_mode == "cover"
        else 1
    )
    # Prepared PNGs are produced just in time and removed after each clip, so
    # only one maximum-sized frame can coexist with the encoded intermediates.
    prepared_frames = (
        min(len(items), 1)
        * settings.width
        * settings.height
        * 3
        * prepared_frame_factor
    )
    seconds = max(duration_ms, 1) / 1000
    quality_factor = max(0.6, (28 - settings.crf) / 8)
    motion_factor = 1.35 if settings.motion_mode != "none" else 1.0
    estimated_video_bitrate = (
        settings.width * settings.height * settings.fps * 0.08 * quality_factor * motion_factor
    )
    encoded_passes = int(
        estimated_video_bitrate * seconds / 8 * ENCODED_TRANSIENT_MULTIPLIER
    )
    audio_and_overhead = int(seconds * 32_000 + 64 * 1024 * 1024)
    return prepared_frames + encoded_passes + audio_and_overhead + reserve_bytes


def storage_reserve_bytes(
    total_bytes: int,
    *,
    minimum_bytes: int = STORAGE_RESERVE_MIN_BYTES,
    maximum_bytes: int = STORAGE_RESERVE_MAX_BYTES,
    reserve_percent: int = STORAGE_RESERVE_PERCENT,
) -> int:
    """Return a bounded reserve derived from the filesystem's total capacity."""
    if total_bytes < 0 or minimum_bytes < 0 or maximum_bytes < minimum_bytes:
        raise ValueError("Некорректные границы резерва свободного места.")
    if not 0 <= reserve_percent <= 100:
        raise ValueError("Доля резерва должна быть от 0 до 100 процентов.")
    proportional = (total_bytes * reserve_percent + 99) // 100
    return min(maximum_bytes, max(minimum_bytes, proportional))


def disk_estimate(
    directory: Path,
    items: list[TimelineItem],
    settings: VideoSettings,
    duration_ms: int,
) -> DiskEstimate:
    usage = shutil.disk_usage(directory)
    reserve = storage_reserve_bytes(usage.total)
    required = estimate_required_disk_bytes(
        items,
        settings,
        duration_ms,
        reserve_bytes=reserve,
    )
    return DiskEstimate(required, usage.free, reserve)
