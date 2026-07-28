from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


ScaleMode = Literal["cover", "fit_blur", "fit_color"]
MotionMode = Literal[
    "none", "zoom_in", "zoom_out", "left_right", "right_left",
    "top_bottom", "bottom_top", "auto", "smart",
]
TransitionMode = Literal["none", "fade", "crossfade_safe"]
EndMode = Literal[
    "extend_last", "black", "trim_to_timeline", "trim_video",
    "pad_silence", "error",
]


@dataclass(frozen=True, slots=True)
class VideoSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    scale_mode: ScaleMode = "cover"
    background_color: str = "#000000"
    motion_mode: MotionMode = "none"
    motion_strength: float = 0.06
    motion_speed: float = 1.0
    alternate_randomly: bool = False
    seed: int = 42
    transition_mode: TransitionMode = "none"
    transition_duration_ms: int = 200
    preset: str = "medium"
    crf: int = 20


@dataclass(frozen=True, slots=True)
class AudioSettings:
    normalize: bool = False
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    volume_percent: int = 100


@dataclass(frozen=True, slots=True)
class RenderSettings:
    video: VideoSettings
    audio: AudioSettings
    end_mode: EndMode
    keep_debug_files: bool = False
    preview_start_ms: int = 0
    preview_end_ms: int | None = None


@dataclass(slots=True)
class RenderResult:
    success: bool
    output_path: Path | None = None
    cancelled: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    media_info: dict[str, object] = field(default_factory=dict)
