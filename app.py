from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import streamlit as st

from config import (
    APP_TITLE,
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    QUALITY_PRESETS,
    SYNC_TOLERANCE_MS,
    VIDEO_SIZES,
)
from models.render import AudioSettings, RenderSettings, VideoSettings
from models.timeline import SourceImage, TimelineItem, ValidationIssue
from services.image_processor import ImageValidationError, create_thumbnail, validate_image
from services.media_probe import MediaProbeError, check_media_tools, probe_audio_duration_ms
from services.render_controller import RenderController
from services.resource_estimator import disk_estimate
from services.settings_validator import validate_render_settings
from services.timeline_builder import build_timeline
from services.timeline_validator import validate_timeline, validate_timeline_for_fps
from services.video_renderer import VideoRenderer, build_render_plan
from services.workspace_manager import WorkspaceLimitError, WorkspaceManager
from utils.file_utils import friendly_os_error, human_file_size, image_data_uri
from utils.time_utils import format_ms, parse_display_time


st.set_page_config(page_title=APP_TITLE, page_icon="🎬", layout="wide")


@st.cache_data(show_spinner=False)
def cached_validate_image(path_text: str, filename: str, modified_ns: int) -> tuple[int, int]:
    del modified_ns
    return validate_image(Path(path_text), filename)


@st.cache_data(show_spinner=False)
def cached_audio_duration(path_text: str, modified_ns: int, ffprobe_text: str) -> int:
    del modified_ns
    return probe_audio_duration_ms(Path(path_text), Path(ffprobe_text))


@st.cache_data(show_spinner=False, ttl=30)
def cached_media_tools(
    ffmpeg_text: str | None,
    ffprobe_text: str | None,
) -> tuple[Path | None, Path | None, list[str]]:
    return check_media_tools(ffmpeg_text, ffprobe_text)


def initialize_state() -> None:
    if "workspace" not in st.session_state:
        st.session_state.workspace = WorkspaceManager()
    if "overrides_ms" not in st.session_state:
        st.session_state.overrides_ms = {}
    if "removed_images" not in st.session_state:
        st.session_state.removed_images = set()
    if "render_controller" not in st.session_state:
        st.session_state.render_controller = RenderController()


def issue_messages(issues: list[ValidationIssue]) -> None:
    for issue in issues:
        (st.error if issue.critical else st.warning)(issue.message)


def size_settings() -> tuple[int, int, list[str]]:
    errors: list[str] = []
    preset_name = st.selectbox(
        "Формат кадра",
        [*VIDEO_SIZES.keys(), "Пользовательский размер"],
        key="video_size_preset",
    )
    if preset_name == "Пользовательский размер":
        left, right = st.columns(2)
        width = int(left.number_input("Ширина", min_value=2, value=1920, step=2))
        height = int(right.number_input("Высота", min_value=2, value=1080, step=2))
    else:
        width, height = VIDEO_SIZES[preset_name]
        st.caption(f"Итоговое разрешение: {width}×{height}")
    if width <= 0 or height <= 0:
        errors.append("Ширина и высота должны быть положительными числами.")
    if width % 2 or height % 2:
        errors.append("Ширина и высота должны быть чётными для H.264/yuv420p.")
    return width, height, errors


def end_mode_widget(audio_ms: int, timeline_ms: int) -> str:
    difference = audio_ms - timeline_ms
    if abs(difference) <= SYNC_TOLERANCE_MS:
        st.success("Длительности совпадают в пределах допуска 50 мс.")
        return "extend_last" if difference >= 0 else "trim_video"
    if difference > 0:
        options = {
            "Продлить последний кадр до окончания аудио": "extend_last",
            "Добавить чёрный экран до окончания аудио": "black",
            "Обрезать аудио и видео по последнему кадру": "trim_to_timeline",
        }
    else:
        options = {
            "Обрезать видео по длительности аудио": "trim_video",
            "Оставить видео и добавить тишину после аудио": "pad_silence",
            "Считать расхождение критической ошибкой": "error",
        }
    label = st.radio("Как завершить ролик", list(options), key="end_mode_label")
    return options[label]


def timeline_editor(items: list[TimelineItem], workspace: WorkspaceManager) -> None:
    st.subheader("4. Таблица таймлайна")
    st.caption("Измените время окончания или отметьте кадры для удаления, затем нажмите «Применить».")
    rows: list[dict[str, object]] = []
    thumbnail_dir = workspace.root / "thumbnails"
    for item in items:
        thumbnail = thumbnail_dir / f"{item.stored_path.stem}.jpg"
        try:
            if not thumbnail.exists():
                create_thumbnail(item.stored_path, thumbnail)
            preview = image_data_uri(thumbnail)
        except (OSError, ImageValidationError):
            preview = ""
        rows.append({
            "ID": item.stored_path.name,
            "№": item.index,
            "Миниатюра": preview,
            "Исходное имя": item.original_filename,
            "Начало": item.start_formatted,
            "Окончание": item.end_formatted,
            "Длительность": item.duration_formatted,
            "Конец, сек": item.end_ms / 1000,
            "Статус": "Ошибка" if item.errors else ("Изменено" if item.manually_overridden else "Готов"),
            "Сообщение": "; ".join(item.errors + item.warnings),
            "Удалить": False,
        })
    with st.form("timeline_editor_form"):
        edited = st.data_editor(
            rows,
            hide_index=True,
            use_container_width=True,
            column_config={
                "ID": None,
                "Миниатюра": st.column_config.ImageColumn("Миниатюра", width="small"),
                "Окончание": st.column_config.TextColumn(
                    "Окончание", help="Формат HH:MM:SS.mmm"
                ),
                "Удалить": st.column_config.CheckboxColumn("Удалить"),
            },
            disabled=[
                "№", "Миниатюра", "Исходное имя", "Начало", "Длительность",
                "Конец, сек", "Статус", "Сообщение",
            ],
            key="timeline_editor",
        )
        apply_changes = st.form_submit_button("Применить изменения таймлайна")
    if apply_changes:
        errors: list[str] = []
        new_overrides = dict(st.session_state.overrides_ms)
        removed = set(st.session_state.removed_images)
        edited_rows = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
        for original, row in zip(rows, edited_rows, strict=True):
            path_key = str(row["ID"])
            if bool(row["Удалить"]):
                removed.add(path_key)
                continue
            try:
                value = parse_display_time(str(row["Окончание"]))
            except ValueError as exc:
                errors.append(f"{row['Исходное имя']}: {exc}")
                continue
            if value != round(float(original["Конец, сек"]) * 1000):
                new_overrides[path_key] = value
        if errors:
            for error in errors:
                st.error(error)
        else:
            st.session_state.overrides_ms = new_overrides
            st.session_state.removed_images = removed
            st.rerun()


def result_panel(controller: RenderController, workspace: WorkspaceManager) -> None:
    status = controller.snapshot()
    if not status.running and status.result is None:
        return
    st.subheader("Прогресс рендеринга")
    fraction = min(1.0, status.current / max(status.total, 1))
    st.progress(
        fraction,
        text=f"{status.stage}: {status.current}/{status.total} ({fraction * 100:.1f}%)",
    )
    st.caption(status.message)
    if status.logs:
        with st.expander("Журнал последних действий"):
            st.code("\n".join(status.logs), language=None)
    if status.running:
        if st.button("Отменить рендеринг", type="secondary"):
            controller.cancel()
            st.warning("Сигнал отмены отправлен. Завершаем активный процесс FFmpeg…")
        return
    result = status.result
    if result is None:
        return
    if result.cancelled:
        st.warning(result.error or "Рендеринг отменён.")
        for warning in result.warnings:
            st.warning(warning)
        return
    if not result.success or result.output_path is None:
        st.error(result.error or "Рендеринг завершился ошибкой.")
        for warning in result.warnings:
            st.warning(warning)
        st.caption(
            "Технический stderr не показывается в интерфейсе. Полный журнал сохранён "
            f"в logs/{workspace.project_id}/ffmpeg.log."
        )
        return
    st.success(f"Видео готово: output/{workspace.project_id}/{result.output_path.name}")
    for warning in result.warnings:
        st.warning(warning)
    info = result.media_info
    st.video(str(result.output_path))
    st.table({
        "Параметр": [
            "Длительность", "Разрешение", "FPS", "Видеокодек",
            "Режим FPS", "Аудиокодек", "Pixel format", "Контейнер", "Размер",
        ],
        "Значение": [
            format_ms(int(info.get("duration_ms", 0))),
            f"{info.get('width', 0)}×{info.get('height', 0)}",
            f"{float(info.get('fps', 0)):.3f}",
            str(info.get("video_codec", "")),
            "постоянный (CFR)" if info.get("is_cfr") else "переменный/не определён",
            str(info.get("audio_codec", "")),
            str(info.get("pixel_format", "")),
            str(info.get("container", "")),
            human_file_size(int(info.get("size_bytes", 0))),
        ],
    })
    with result.output_path.open("rb") as video_file:
        st.download_button(
            "Скачать MP4", video_file, file_name=result.output_path.name, mime="video/mp4"
        )
    if st.button("Открыть папку output"):
        try:
            subprocess.Popen(["explorer.exe", str(result.output_path.parent)], shell=False)
        except OSError:
            st.error("Не удалось открыть Проводник. Откройте папку output вручную.")


initialize_state()
workspace: WorkspaceManager = st.session_state.workspace
controller: RenderController = st.session_state.render_controller

st.title(APP_TITLE)
st.subheader("Время окончания каждого кадра берётся из имени изображения")
st.info(
    "Пример: файл [1-15]_scene.jpg будет показываться до отметки 01:15. "
    "Начало программа рассчитает автоматически по предыдущему кадру."
)

with st.sidebar:
    st.header("FFmpeg")
    ffmpeg_custom = st.text_input("Путь к ffmpeg.exe (необязательно)", key="ffmpeg_custom")
    ffprobe_custom = st.text_input("Путь к ffprobe.exe (необязательно)", key="ffprobe_custom")
    st.caption(f"ID проекта: {workspace.project_id}")
    if st.button("Создать новый проект", disabled=controller.is_running):
        try:
            workspace.cleanup_project(keep_output=True)
        except OSError:
            st.error("Не удалось очистить временные файлы. Закройте программы, использующие их.")
        else:
            st.session_state.clear()
            st.rerun()

ffmpeg_path, ffprobe_path, tool_errors = cached_media_tools(
    ffmpeg_custom or None, ffprobe_custom or None
)

st.header("1. Загрузка изображений")
st.caption("До 1000 изображений; один файл — до 100 МБ, общий объём — до 2 ГБ.")
source_mode = st.radio(
    "Источник изображений", ["Загрузить файлы", "Локальная папка"], horizontal=True
)
sources: list[SourceImage] = []
load_issues: list[ValidationIssue] = []
if source_mode == "Загрузить файлы":
    uploaded_images = st.file_uploader(
        "Выберите изображения в любом порядке",
        type=[extension.lstrip(".") for extension in sorted(IMAGE_EXTENSIONS)],
        accept_multiple_files=True,
    )
    if uploaded_images:
        try:
            sources = workspace.save_uploaded_images(uploaded_images)
        except OSError as exc:
            load_issues.append(ValidationIssue(friendly_os_error("Сохранение изображений", exc)))
        except (WorkspaceLimitError, ValueError) as exc:
            load_issues.append(ValidationIssue(f"Не удалось сохранить изображения: {exc}"))
else:
    folder_text = st.text_input("Полный путь к папке с изображениями")
    if folder_text:
        try:
            sources = workspace.import_folder(Path(folder_text))
            if not sources:
                load_issues.append(ValidationIssue("В выбранной папке нет поддерживаемых изображений."))
        except OSError as exc:
            load_issues.append(ValidationIssue(friendly_os_error("Чтение папки", exc)))
        except ValueError as exc:
            load_issues.append(ValidationIssue(f"Не удалось прочитать папку: {exc}"))

if st.session_state.removed_images:
    sources = [s for s in sources if s.stored_path.name not in st.session_state.removed_images]
    if st.button("Вернуть удалённые изображения"):
        st.session_state.removed_images = set()
        st.rerun()

st.header("2. Загрузка аудио")
st.caption("Один аудиофайл размером до 200 МБ.")
uploaded_audio = st.file_uploader(
    "Выберите один файл с полной озвучкой",
    type=[extension.lstrip(".") for extension in sorted(AUDIO_EXTENSIONS)],
    accept_multiple_files=False,
)
audio_path: Path | None = None
if uploaded_audio is not None:
    if Path(uploaded_audio.name).suffix.lower() not in AUDIO_EXTENSIONS:
        load_issues.append(ValidationIssue(f"Неподдерживаемый аудиоформат: {uploaded_audio.name}."))
    else:
        try:
            audio_path = workspace.save_uploaded_audio(uploaded_audio)
        except OSError as exc:
            load_issues.append(ValidationIssue(friendly_os_error("Сохранение аудио", exc)))
        except (WorkspaceLimitError, ValueError) as exc:
            load_issues.append(ValidationIssue(f"Не удалось сохранить аудио: {exc}"))

st.header("3. Проверка временных меток и файлов")
items, parse_issues = build_timeline(sources, st.session_state.overrides_ms)
validation_issues = validate_timeline(items) if sources else [ValidationIssue("Загрузите хотя бы одно изображение.")]
valid_paths = {item.stored_path.name for item in items}
invalid_sources = [source for source in sources if source.stored_path.name not in valid_paths]
if invalid_sources:
    labels = {source.stored_path.name: source.original_filename for source in invalid_sources}
    selected_invalid = st.multiselect(
        "Проблемные изображения можно удалить из проекта",
        options=list(labels),
        format_func=lambda value: labels[value],
    )
    if st.button("Удалить выбранные проблемные изображения", disabled=not selected_invalid):
        st.session_state.removed_images.update(selected_invalid)
        st.rerun()
image_issues: list[ValidationIssue] = []
for item in items:
    try:
        cached_validate_image(
            str(item.stored_path), item.original_filename, item.stored_path.stat().st_mtime_ns
        )
    except (ImageValidationError, OSError) as exc:
        item.is_valid = False
        item.errors.append(str(exc))
        image_issues.append(ValidationIssue(str(exc), item.original_filename))

audio_duration_ms: int | None = None
if audio_path is None:
    load_issues.append(ValidationIssue("Загрузите аудиофайл."))
elif ffprobe_path is not None:
    try:
        audio_duration_ms = cached_audio_duration(
            str(audio_path), audio_path.stat().st_mtime_ns, str(ffprobe_path)
        )
    except (MediaProbeError, OSError) as exc:
        load_issues.append(ValidationIssue(str(exc)))

all_issues = load_issues + parse_issues + validation_issues + image_issues
all_issues.extend(ValidationIssue(message) for message in tool_errors)
if all_issues:
    issue_messages(all_issues)
else:
    st.success(f"Проверено кадров: {len(items)}. Критических ошибок нет.")

if items:
    timeline_editor(items, workspace)
else:
    st.header("4. Таблица таймлайна")
    st.caption("Появится после добавления хотя бы одного корректного изображения.")

st.header("5. Настройки видео")
width, height, settings_errors = size_settings()
fps = st.selectbox("FPS", [24, 25, 30, 60], index=2)
quality_name = st.selectbox("Качество", list(QUALITY_PRESETS), index=1)
preset, crf = QUALITY_PRESETS[quality_name]

st.header("6. Настройки обработки изображений")
scale_labels = {
    "Заполнить экран с центральной обрезкой": "cover",
    "Вписать полностью с размытым фоном": "fit_blur",
    "Вписать полностью с цветным фоном": "fit_color",
}
scale_label = st.radio("Масштабирование", list(scale_labels))
background_color = st.color_picker("Цвет фона", "#000000", disabled=scale_labels[scale_label] != "fit_color")
motion_labels = {
    "Без движения": "none", "Медленное приближение": "zoom_in",
    "Медленное отдаление": "zoom_out", "Слева направо": "left_right",
    "Справа налево": "right_left", "Сверху вниз": "top_bottom",
    "Снизу вверх": "bottom_top", "Автоматическое чередование": "auto",
}
motion_label = st.selectbox("Ken Burns", list(motion_labels))
motion_columns = st.columns(4)
motion_strength = motion_columns[0].slider("Сила", 0.01, 0.30, 0.06, 0.01)
motion_speed = motion_columns[1].slider("Скорость", 0.25, 2.0, 1.0, 0.05)
alternate_randomly = motion_columns[2].checkbox("Случайно", value=False)
seed = int(motion_columns[3].number_input("Seed", value=42, step=1))

st.header("7. Настройки переходов")
transition_labels = {
    "Без переходов": "none", "Fade внутри кадра": "fade",
    "Crossfade (синхробезопасный fade)": "crossfade_safe",
}
transition_label = st.radio("Переход", list(transition_labels), horizontal=True)
transition_seconds = st.slider("Длительность перехода, сек", 0.0, 2.0, 0.2, 0.05)
if transition_labels[transition_label] == "crossfade_safe":
    st.caption(
        "Для точной синхронизации используется затухание внутри границ каждого кадра без перекрытия клипов."
    )

st.header("8. Настройки аудио")
audio_columns = st.columns(4)
normalize = audio_columns[0].checkbox("Нормализация", value=False)
volume = audio_columns[1].slider("Громкость, %", 0, 200, 100)
fade_in = audio_columns[2].number_input("Появление, сек", 0.0, 10.0, 0.0, 0.1)
fade_out = audio_columns[3].number_input("Исчезновение, сек", 0.0, 10.0, 0.0, 0.1)

st.header("9. Настройки окончания ролика")
end_mode = "extend_last"
if audio_duration_ms is not None and items:
    timeline_end_ms = items[-1].end_ms
    difference_ms = audio_duration_ms - timeline_end_ms
    metric_columns = st.columns(3)
    metric_columns[0].metric("Длительность аудио", format_ms(audio_duration_ms))
    metric_columns[1].metric("Конец последнего кадра", format_ms(timeline_end_ms))
    metric_columns[2].metric("Разница", format_ms(abs(difference_ms)), delta=f"{difference_ms / 1000:+.3f} сек")
    end_mode = end_mode_widget(audio_duration_ms, timeline_end_ms)

video_settings = VideoSettings(
    width=width, height=height, fps=int(fps),
    scale_mode=scale_labels[scale_label],  # type: ignore[arg-type]
    background_color=background_color,
    motion_mode=motion_labels[motion_label],  # type: ignore[arg-type]
    motion_strength=motion_strength, motion_speed=motion_speed,
    alternate_randomly=alternate_randomly, seed=seed,
    transition_mode=transition_labels[transition_label],  # type: ignore[arg-type]
    transition_duration_ms=round(transition_seconds * 1000), preset=preset, crf=crf,
)
audio_settings = AudioSettings(
    normalize=normalize, fade_in_ms=round(fade_in * 1000),
    fade_out_ms=round(fade_out * 1000), volume_percent=volume,
)
render_settings = RenderSettings(video_settings, audio_settings, end_mode)  # type: ignore[arg-type]

render_setting_issues = validate_render_settings(render_settings)
fps_issues = validate_timeline_for_fps(items, int(fps)) if items else []
critical_errors = bool(all_issues or settings_errors or render_setting_issues or fps_issues)
for error in settings_errors:
    st.error(error)
issue_messages(render_setting_issues + fps_issues)
if end_mode == "error" and audio_duration_ms is not None and items and items[-1].end_ms > audio_duration_ms:
    critical_errors = True
    st.error("Выбран критический режим: исправьте таймлайн перед рендерингом.")

if audio_duration_ms is not None and items and not render_setting_issues:
    try:
        _segments, _offset, estimated_duration_ms, _pad = build_render_plan(
            items, audio_duration_ms, render_settings
        )
        resources = disk_estimate(
            workspace.root, items, video_settings, estimated_duration_ms
        )
        disk_columns = st.columns(2)
        disk_columns[0].metric(
            "Оценка места для рендера", human_file_size(resources.required_bytes)
        )
        disk_columns[1].metric("Свободно на диске", human_file_size(resources.free_bytes))
        if not resources.sufficient:
            critical_errors = True
            st.error(
                "Свободного места может не хватить. Освободите диск либо уменьшите "
                "разрешение, качество или длительность."
            )
        else:
            st.caption("Оценка включает резерв 512 МБ и промежуточные клипы.")
    except ValueError as exc:
        critical_errors = True
        st.error(str(exc))

st.header("10. Предпросмотр")
preview_columns = st.columns(3)
preview_start = preview_columns[0].number_input("Начало, сек", min_value=0.0, value=0.0, step=1.0)
preview_duration = preview_columns[1].selectbox("Длительность", [15, 30], index=0)
preview_end_custom = preview_columns[2].number_input(
    "Конец диапазона, сек (0 = авто)", min_value=0.0, value=0.0, step=1.0
)
start_ms = round(preview_start * 1000)
end_ms = round(preview_end_custom * 1000) if preview_end_custom > 0 else start_ms + preview_duration * 1000

can_render = not critical_errors and not controller.is_running and audio_path is not None and audio_duration_ms is not None
if st.button("Создать предпросмотр", disabled=not can_render):
    assert ffmpeg_path is not None and ffprobe_path is not None and audio_path is not None and audio_duration_ms is not None
    preview_settings = replace(render_settings, preview_start_ms=start_ms, preview_end_ms=end_ms)
    renderer = VideoRenderer(ffmpeg_path, ffprobe_path, workspace)
    controller.start(
        renderer.render, items, audio_path, audio_duration_ms, preview_settings,
        output_name="preview.mp4",
    )
    st.rerun()

st.header("11. Финальный рендеринг")
keep_debug = st.checkbox("Сохранять промежуточные файлы для отладки", value=False)
if st.button("Собрать финальное видео", type="primary", disabled=not can_render):
    assert ffmpeg_path is not None and ffprobe_path is not None and audio_path is not None and audio_duration_ms is not None
    final_settings = replace(render_settings, keep_debug_files=keep_debug)
    renderer = VideoRenderer(ffmpeg_path, ffprobe_path, workspace)
    controller.start(
        renderer.render, items, audio_path, audio_duration_ms, final_settings,
        output_name="final_video.mp4",
    )
    st.rerun()

st.header("12. Результат")


@st.fragment(run_every=1.0)
def live_result() -> None:
    result_panel(controller, workspace)


live_result()
