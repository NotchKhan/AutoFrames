from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ProjectStatus = Literal[
    "draft",
    "ready",
    "queued",
    "rendering",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
]


class ProjectResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    expires_at: str


class UploadResponse(BaseModel):
    project_id: str
    uploaded_count: int
    total_images: int
    audio_uploaded: bool
    status: ProjectStatus


class ValidationIssueResponse(BaseModel):
    message: str
    filename: str | None = None
    critical: bool = True


class TimelineRowResponse(BaseModel):
    index: int
    image_id: str
    original_filename: str
    parsed_timestamp: str
    start_ms: int
    end_ms: int
    duration_ms: int
    start_formatted: str
    end_formatted: str
    duration_formatted: str
    is_valid: bool
    errors: list[str]
    warnings: list[str]


class TimelineResponse(BaseModel):
    project_id: str
    is_valid: bool
    items: list[TimelineRowResponse]
    issues: list[ValidationIssueResponse]
    audio_uploaded: bool
    audio_duration_ms: int | None
    audio_duration_formatted: str | None
    timeline_end_ms: int | None
    timeline_end_formatted: str | None
    difference_ms: int | None


class VideoSettingsPayload(BaseModel):
    width: int = Field(default=1920, gt=0, le=8192)
    height: int = Field(default=1080, gt=0, le=8192)
    fps: Literal[24, 25, 30, 60] = 30
    scale_mode: Literal["cover", "fit_blur", "fit_color"] = "cover"
    background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    motion_mode: Literal[
        "none",
        "zoom_in",
        "zoom_out",
        "left_right",
        "right_left",
        "top_bottom",
        "bottom_top",
        "auto",
    ] = "none"
    motion_strength: float = Field(default=0.06, ge=0, le=0.35)
    motion_speed: float = Field(default=1.0, gt=0, le=5)
    alternate_randomly: bool = False
    seed: int = 42
    transition_mode: Literal["none", "fade", "crossfade_safe"] = "none"
    transition_duration_ms: int = Field(default=200, ge=0, le=2000)
    preset: Literal["veryfast", "medium", "slow"] = "medium"
    crf: int = Field(default=20, ge=0, le=51)


class AudioSettingsPayload(BaseModel):
    normalize: bool = False
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    volume_percent: int = Field(default=100, ge=0, le=200)


class RenderRequest(BaseModel):
    video: VideoSettingsPayload = Field(default_factory=VideoSettingsPayload)
    audio: AudioSettingsPayload = Field(default_factory=AudioSettingsPayload)
    end_mode: Literal[
        "extend_last",
        "black",
        "trim_to_timeline",
        "trim_video",
        "pad_silence",
        "error",
    ] = "extend_last"
    keep_debug_files: bool = False
    preview_start_ms: int = Field(default=0, ge=0)
    preview_end_ms: int | None = Field(default=None, gt=0)


class RenderAcceptedResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    message: str


class ProgressResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    stage: str
    progress_percent: float
    current: int
    total: int
    completed_operations: int
    message: str


class StatusResponse(ProgressResponse):
    recent_logs: list[str]
    error: str | None
    result_ready: bool
    media_info: dict[str, Any]


class DeleteResponse(BaseModel):
    project_id: str
    deleted: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    ffmpeg: bool
    ffprobe: bool
