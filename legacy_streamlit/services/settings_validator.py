from __future__ import annotations

from models.render import RenderSettings, VideoSettings
from models.timeline import ValidationIssue
from config import MAX_VIDEO_DIMENSION


_FPS_VALUES = {24, 25, 30, 60}
_PRESETS = {"veryfast", "medium", "slow"}
_SCALE_MODES = {"cover", "fit_blur", "fit_color"}
_MOTION_MODES = {
    "none", "zoom_in", "zoom_out", "left_right", "right_left",
    "top_bottom", "bottom_top", "auto",
}
_TRANSITION_MODES = {"none", "fade", "crossfade_safe"}


def validate_video_settings(settings: VideoSettings) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if settings.width <= 0 or settings.height <= 0:
        issues.append(ValidationIssue("Ширина и высота видео должны быть положительными."))
    if settings.width % 2 or settings.height % 2:
        issues.append(ValidationIssue("Ширина и высота должны быть чётными для H.264/yuv420p."))
    if settings.width > MAX_VIDEO_DIMENSION or settings.height > MAX_VIDEO_DIMENSION:
        issues.append(ValidationIssue(
            f"Максимальная ширина или высота — {MAX_VIDEO_DIMENSION} пикселей."
        ))
    if settings.fps not in _FPS_VALUES:
        issues.append(ValidationIssue("FPS должен быть одним из значений: 24, 25, 30 или 60."))
    if settings.preset not in _PRESETS or not 0 <= settings.crf <= 51:
        issues.append(ValidationIssue("Некорректный профиль качества FFmpeg (preset/CRF)."))
    if settings.scale_mode not in _SCALE_MODES:
        issues.append(ValidationIssue("Выбран неизвестный режим масштабирования изображения."))
    if settings.motion_mode not in _MOTION_MODES:
        issues.append(ValidationIssue("Выбран неизвестный эффект движения."))
    if not 0 <= settings.motion_strength <= 0.35 or settings.motion_speed <= 0:
        issues.append(ValidationIssue("Некорректная сила или скорость движения кадра."))
    if settings.transition_mode not in _TRANSITION_MODES:
        issues.append(ValidationIssue("Выбран неизвестный режим перехода."))
    if not 0 <= settings.transition_duration_ms <= 2_000:
        issues.append(ValidationIssue("Длительность перехода должна быть от 0 до 2 секунд."))
    return issues


def validate_render_settings(settings: RenderSettings) -> list[ValidationIssue]:
    issues = validate_video_settings(settings.video)
    if not 0 <= settings.audio.volume_percent <= 200:
        issues.append(ValidationIssue("Громкость должна быть от 0 до 200 процентов."))
    if settings.audio.fade_in_ms < 0 or settings.audio.fade_out_ms < 0:
        issues.append(ValidationIssue("Длительность аудиозатухания не может быть отрицательной."))
    if settings.preview_start_ms < 0:
        issues.append(ValidationIssue("Начало предпросмотра не может быть отрицательным."))
    if (
        settings.preview_end_ms is not None
        and settings.preview_end_ms <= settings.preview_start_ms
    ):
        issues.append(ValidationIssue("Конец предпросмотра должен быть позже его начала."))
    return issues
