from __future__ import annotations

import random
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from config import OUTPUT_ROOT, SYNC_TOLERANCE_MS
from models.render import RenderResult, RenderSettings
from models.timeline import TimelineItem
from services.ffmpeg_builder import clip_command, concat_command, mux_command
from services.image_processor import create_black_frame, prepare_image
from services.media_probe import MediaProbeError, summarize_output
from services.resource_estimator import disk_estimate
from services.settings_validator import validate_render_settings
from services.timeline_validator import validate_timeline, validate_timeline_for_fps
from services.workspace_manager import WorkspaceManager
from utils.file_utils import friendly_os_error, human_file_size
from utils.process_utils import ProcessCancelled, ProcessExecutionError, run_process
from utils.time_utils import frame_index, format_ms


ProgressCallback = Callable[[str, int, int, str], None]


@dataclass(frozen=True, slots=True)
class RenderSegment:
    source_path: Path | None
    original_filename: str
    start_ms: int
    end_ms: int
    source_index: int


def _base_segments(items: Sequence[TimelineItem]) -> list[RenderSegment]:
    return [
        RenderSegment(item.stored_path, item.original_filename, item.start_ms, item.end_ms, item.index)
        for item in items
    ]


def build_render_plan(
    items: Sequence[TimelineItem],
    audio_duration_ms: int,
    settings: RenderSettings,
) -> tuple[list[RenderSegment], int, int, bool]:
    if not items:
        raise ValueError("Таймлайн пуст.")
    segments = _base_segments(items)
    timeline_end = items[-1].end_ms
    difference = audio_duration_ms - timeline_end
    pad_silence = False

    if abs(difference) <= SYNC_TOLERANCE_MS:
        target_end = audio_duration_ms
        if difference > 0:
            last = segments[-1]
            segments[-1] = RenderSegment(
                last.source_path, last.original_filename, last.start_ms,
                audio_duration_ms, last.source_index,
            )
    elif difference > 0:
        if settings.end_mode == "extend_last":
            last = segments[-1]
            segments[-1] = RenderSegment(
                last.source_path, last.original_filename, last.start_ms, audio_duration_ms, last.source_index
            )
            target_end = audio_duration_ms
        elif settings.end_mode == "black":
            segments.append(RenderSegment(None, "Чёрный экран", timeline_end, audio_duration_ms, len(segments) + 1))
            target_end = audio_duration_ms
        elif settings.end_mode == "trim_to_timeline":
            target_end = timeline_end
        else:
            raise ValueError("Выбранный режим окончания не подходит: аудио длиннее таймлайна.")
    else:
        if settings.end_mode == "trim_video":
            target_end = audio_duration_ms
        elif settings.end_mode == "pad_silence":
            target_end = timeline_end
            pad_silence = True
        elif settings.end_mode == "error":
            raise ValueError(
                "Последний кадр заканчивается позже аудио. Исправьте таймлайн или выберите другой режим."
            )
        else:
            raise ValueError("Выбранный режим окончания не подходит: таймлайн длиннее аудио.")

    start = max(0, settings.preview_start_ms)
    requested_end = settings.preview_end_ms if settings.preview_end_ms is not None else target_end
    end = min(target_end, requested_end)
    if start >= end:
        raise ValueError("Диапазон предпросмотра пуст или находится за пределами ролика.")

    clipped: list[RenderSegment] = []
    for segment in segments:
        clipped_start = max(segment.start_ms, start)
        clipped_end = min(segment.end_ms, end)
        if clipped_end > clipped_start:
            clipped.append(RenderSegment(
                segment.source_path, segment.original_filename,
                clipped_start - start, clipped_end - start, segment.source_index,
            ))
    return clipped, start, end - start, pad_silence


class VideoRenderer:
    def __init__(
        self,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        workspace: WorkspaceManager,
    ) -> None:
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.workspace = workspace

    def render(
        self,
        items: Sequence[TimelineItem],
        audio_path: Path,
        audio_duration_ms: int,
        settings: RenderSettings,
        output_name: str = "final_video.mp4",
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RenderResult:
        callback = progress or (lambda _stage, _current, _total, _message: None)
        cancel = cancel_event or threading.Event()
        log_path = self.workspace.log_dir / "ffmpeg.log"
        warnings: list[str] = []
        try:
            callback("Проверка файлов", 0, len(items), "Построение плана рендеринга")
            if audio_duration_ms <= 0:
                raise ValueError("Длительность аудио должна быть больше нуля.")
            issues = (
                validate_render_settings(settings)
                + validate_timeline(items)
                + validate_timeline_for_fps(items, settings.video.fps)
            )
            if issues:
                raise ValueError(issues[0].message)
            output_path = self.workspace.output_path(output_name)
            self.workspace.require_owned_path(audio_path, self.workspace.uploads_dir)
            for item in items:
                self.workspace.require_owned_path(item.stored_path, self.workspace.uploads_dir)
            segments, audio_offset, requested_duration_ms, pad_silence = build_render_plan(
                items, audio_duration_ms, settings
            )
            resources = disk_estimate(
                self.workspace.root, list(items), settings.video, requested_duration_ms
            )
            if not resources.sufficient:
                raise ValueError(
                    "Недостаточно свободного места для безопасного рендеринга. "
                    f"Требуется примерно {human_file_size(resources.required_bytes)}, "
                    f"доступно {human_file_size(resources.free_bytes)}. "
                    "Освободите место или уменьшите разрешение/длительность."
                )
            self.workspace.clear_intermediates()
            fps = settings.video.fps
            total_frames = frame_index(requested_duration_ms, fps)
            if total_frames <= 0:
                raise ValueError("Диапазон короче одного видеокадра.")

            quantized: list[tuple[RenderSegment, int, int]] = []
            for segment in segments:
                first = frame_index(segment.start_ms, fps)
                last = frame_index(segment.end_ms, fps)
                frames = last - first
                if frames <= 0:
                    raise ValueError(
                        f"Кадр «{segment.original_filename}» короче одного физического кадра "
                        f"при {fps} FPS. Увеличьте его длительность: пропуск нарушил бы синхронизацию."
                    )
                quantized.append((segment, frames, first))
            if not quantized:
                raise ValueError("После привязки к выбранному FPS не осталось кадров для рендеринга.")
            if sum(frames for _segment, frames, _first in quantized) != total_frames:
                raise ValueError(
                    "После привязки таймлайна к FPS возник разрыв. Исправьте слишком короткие кадры."
                )

            callback("Подготовка изображений", 0, len(quantized), "Обработка EXIF и масштаба")
            prepared_by_source: dict[Path | None, Path] = {}
            for position, (segment, _frames, _first) in enumerate(quantized, start=1):
                if cancel.is_set():
                    raise ProcessCancelled("Операция отменена пользователем.")
                if segment.source_path not in prepared_by_source:
                    prepared = self.workspace.prepared_dir / f"{position:06d}.png"
                    if segment.source_path is None:
                        create_black_frame(prepared, settings.video.width, settings.video.height)
                    else:
                        prepare_image(segment.source_path, prepared, settings.video)
                    prepared_by_source[segment.source_path] = prepared
                callback(
                    "Подготовка изображений", position, len(quantized),
                    f"Подготовлен кадр {position}/{len(quantized)}: {segment.original_filename}",
                )

            effects = [
                "zoom_in", "zoom_out", "left_right", "right_left", "top_bottom", "bottom_top"
            ]
            rng = random.Random(settings.video.seed)
            clip_paths: list[Path] = []
            callback("Создание видеоклипов", 0, len(quantized), "Запуск FFmpeg")
            for position, (segment, frames, _first) in enumerate(quantized, start=1):
                if settings.video.motion_mode == "auto":
                    effect = rng.choice(effects) if settings.video.alternate_randomly else effects[(position - 1) % len(effects)]
                else:
                    effect = settings.video.motion_mode
                clip = self.workspace.clips_dir / f"{position:06d}.mp4"
                duration_ms = round(frames * 1000 / fps)
                run_process(
                    clip_command(
                        self.ffmpeg, prepared_by_source[segment.source_path], clip,
                        settings.video, effect, frames, duration_ms,
                    ),
                    log_path, cancel,
                )
                clip_paths.append(clip)
                callback(
                    "Создание видеоклипов", position, len(quantized),
                    f"Создан клип {position}/{len(quantized)}: {segment.original_filename}",
                )

            # Пути concat demuxer разрешает относительно самого списка.
            concat_file = self.workspace.clips_dir / "concat.txt"
            concat_file.write_text(
                "".join(f"file '{clip.name}'\n" for clip in clip_paths), encoding="utf-8"
            )
            silent_video = self.workspace.render_dir / "silent_video.mp4"
            callback("Объединение видеоклипов", 0, 1, "Объединение без повторного кодирования")
            run_process(
                concat_command(self.ffmpeg, concat_file, silent_video),
                log_path, cancel, cwd=self.workspace.clips_dir,
            )
            callback("Объединение видеоклипов", 1, 1, "Видеоряд объединён")

            temporary_output = self.workspace.render_dir / (output_path.stem + ".partial.mp4")
            actual_target_ms = round(total_frames * 1000 / fps)
            audio_remaining = max(0, audio_duration_ms - audio_offset)
            needs_padding = pad_silence or audio_remaining < actual_target_ms - SYNC_TOLERANCE_MS
            callback("Добавление аудио", 0, 1, "Кодирование аудио в AAC")
            run_process(
                mux_command(
                    self.ffmpeg, silent_video, audio_path, temporary_output, settings.audio,
                    actual_target_ms, audio_offset, needs_padding,
                ),
                log_path, cancel,
            )
            callback("Добавление аудио", 1, 1, "Аудио добавлено без изменения скорости")

            callback("Финальная проверка", 0, 1, "Проверка MP4 через ffprobe")
            media_info = summarize_output(temporary_output, self.ffprobe)
            mismatch = abs(int(media_info["duration_ms"]) - actual_target_ms)
            allowed = max(2 * 1000 / fps, SYNC_TOLERANCE_MS)
            if mismatch > allowed:
                warnings.append(
                    f"Фактическая длительность отличается от ожидаемой на {format_ms(mismatch)}."
                )
            fatal_media_errors: list[str] = []
            if not media_info["has_video"]:
                fatal_media_errors.append("не найден видеопоток")
            if not media_info["has_audio"]:
                fatal_media_errors.append("не найден аудиопоток")
            if media_info["video_codec"] != "h264":
                fatal_media_errors.append("видеокодек не H.264")
            if media_info["audio_codec"] != "aac":
                fatal_media_errors.append("аудиокодек не AAC")
            if media_info["pixel_format"] != "yuv420p":
                fatal_media_errors.append("pixel format не yuv420p")
            if not media_info["is_cfr"]:
                fatal_media_errors.append("частота кадров не является постоянной")
            if "mp4" not in str(media_info["container"]).split(","):
                fatal_media_errors.append("контейнер не MP4")
            if (
                media_info["width"] != settings.video.width
                or media_info["height"] != settings.video.height
            ):
                fatal_media_errors.append("разрешение не совпадает с настройками")
            if abs(float(media_info["fps"]) - settings.video.fps) > 0.01:
                fatal_media_errors.append("FPS не совпадает с настройками")
            if fatal_media_errors:
                raise ValueError(
                    "Финальная проверка MP4 не пройдена: "
                    + "; ".join(fatal_media_errors)
                    + ". Подробности сохранены в журнале проекта."
                )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_output.replace(output_path)
            if output_name == "final_video.mp4":
                # Уникальная копия остаётся в каталоге проекта, а этот атомарно
                # обновляемый файл выполняет обещанный простой путь output/final_video.mp4.
                latest_temporary = OUTPUT_ROOT / f".{self.workspace.project_id}.latest.partial.mp4"
                try:
                    shutil.copy2(output_path, latest_temporary)
                    latest_temporary.replace(OUTPUT_ROOT / "final_video.mp4")
                except OSError:
                    latest_temporary.unlink(missing_ok=True)
                    warnings.append(
                        "Не удалось обновить копию output/final_video.mp4, но уникальный "
                        "файл проекта успешно создан и проверен."
                    )
            callback(
                "Финальная проверка", 1, 1,
                f"Готово: {format_ms(int(media_info['duration_ms']))}, {human_file_size(output_path.stat().st_size)}",
            )
            if not settings.keep_debug_files:
                try:
                    self.workspace.clear_intermediates()
                except (OSError, ValueError):
                    warnings.append(
                        "Видео готово, но не удалось полностью очистить промежуточные файлы. "
                        "Закройте использующие их программы и создайте новый проект."
                    )
            callback("Готово", 1, 1, f"output/{self.workspace.project_id}/{output_name}")
            return RenderResult(True, output_path, warnings=warnings, media_info=media_info)
        except ProcessCancelled:
            try:
                self.workspace.clear_intermediates()
            except (OSError, ValueError):
                warnings.append("Не удалось полностью очистить промежуточные файлы после отмены.")
            return RenderResult(
                False,
                cancelled=True,
                error="Рендеринг отменён пользователем.",
                warnings=warnings,
            )
        except (ProcessExecutionError, MediaProbeError, OSError, ValueError) as exc:
            if not settings.keep_debug_files:
                try:
                    self.workspace.clear_intermediates()
                except (OSError, ValueError):
                    warnings.append(
                        "Не удалось полностью очистить промежуточные файлы после ошибки. "
                        "Закройте использующие их программы и создайте новый проект."
                    )
            message = (
                friendly_os_error("Рендеринг", exc)
                if isinstance(exc, OSError)
                else str(exc)
            )
            return RenderResult(False, error=message, warnings=warnings)
