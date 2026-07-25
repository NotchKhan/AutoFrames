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
from services.media_probe import summarize_output
from services.workspace_manager import WorkspaceManager
from utils.file_utils import human_file_size
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
            segments, audio_offset, requested_duration_ms, pad_silence = build_render_plan(
                items, audio_duration_ms, settings
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
                    warnings.append(
                        f"Кадр «{segment.original_filename}» короче одного кадра при {fps} FPS и был пропущен."
                    )
                    continue
                quantized.append((segment, frames, first))
            if not quantized:
                raise ValueError("После привязки к выбранному FPS не осталось кадров для рендеринга.")

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

            output_path = self.workspace.output_dir / output_name
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
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_output.replace(output_path)
            callback("Добавление аудио", 1, 1, "Аудио добавлено без изменения скорости")

            callback("Финальная проверка", 0, 1, "Проверка MP4 через ffprobe")
            media_info = summarize_output(output_path, self.ffprobe)
            mismatch = abs(int(media_info["duration_ms"]) - actual_target_ms)
            allowed = max(2 * 1000 / fps, SYNC_TOLERANCE_MS)
            if mismatch > allowed:
                warnings.append(
                    f"Фактическая длительность отличается от ожидаемой на {format_ms(mismatch)}."
                )
            if media_info["video_codec"] != "h264":
                warnings.append("Итоговый видеокодек отличается от H.264.")
            if media_info["audio_codec"] != "aac":
                warnings.append("Итоговый аудиокодек отличается от AAC.")
            if output_name == "final_video.mp4":
                # Уникальная копия остаётся в каталоге проекта, а этот атомарно
                # обновляемый файл выполняет обещанный простой путь output/final_video.mp4.
                latest_temporary = OUTPUT_ROOT / f".{self.workspace.project_id}.latest.partial.mp4"
                shutil.copy2(output_path, latest_temporary)
                latest_temporary.replace(OUTPUT_ROOT / "final_video.mp4")
            callback(
                "Финальная проверка", 1, 1,
                f"Готово: {format_ms(int(media_info['duration_ms']))}, {human_file_size(output_path.stat().st_size)}",
            )
            if not settings.keep_debug_files:
                self.workspace.clear_intermediates()
            callback("Готово", 1, 1, str(output_path))
            return RenderResult(True, output_path, warnings=warnings, media_info=media_info)
        except ProcessCancelled:
            self.workspace.clear_intermediates()
            return RenderResult(False, cancelled=True, error="Рендеринг отменён пользователем.")
        except (ProcessExecutionError, OSError, ValueError) as exc:
            return RenderResult(False, error=str(exc), warnings=warnings)
