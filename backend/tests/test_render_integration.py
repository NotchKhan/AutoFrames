from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from models.render import AudioSettings, RenderSettings, VideoSettings
from models.timeline import SourceImage
from services.audio_analyzer import detect_audio_pauses, prepare_transcription_audio
from services.media_probe import (
    check_media_tools,
    probe_audio_duration_ms,
    summarize_output,
)
from services.timeline_builder import build_timeline
from services.video_renderer import VideoRenderer
from services.workspace_manager import WorkspaceManager
from utils.process_utils import run_process


def media_binaries() -> tuple[Path, Path]:
    ffmpeg, ffprobe, errors = check_media_tools()
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe недоступны: " + "; ".join(errors))
    return ffmpeg, ffprobe


def assert_color(pixel: tuple[int, ...], expected: str) -> None:
    red, green, blue = pixel[:3]
    if expected == "red":
        assert red > 180 and green < 90 and blue < 90
    elif expected == "green":
        assert green > 140 and red < 90 and blue < 90
    elif expected == "blue":
        assert blue > 180 and red < 90 and green < 90
    else:
        assert red > 170 and green > 170 and blue < 100


@pytest.mark.integration
@pytest.mark.parametrize("extension", ["mp3", "wav", "m4a", "aac", "ogg", "flac"])
def test_supported_audio_formats_with_real_ffprobe(
    tmp_path: Path,
    extension: str,
) -> None:
    ffmpeg, ffprobe = media_binaries()
    audio = tmp_path / f"проверка формата.{extension}"
    run_process([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25",
        audio,
    ], tmp_path / "audio-formats.log")
    duration = probe_audio_duration_ms(audio, ffprobe)
    assert 200 <= duration <= 400


@pytest.mark.integration
def test_pause_detection_and_transcription_preparation_with_real_ffmpeg(tmp_path: Path) -> None:
    ffmpeg, ffprobe = media_binaries()
    audio = tmp_path / "voice-with-pause.wav"
    run_process([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.8",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=0.6",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=0.8",
        "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
        "-map", "[out]", audio,
    ], tmp_path / "generate-pause.log")

    pauses = detect_audio_pauses(audio, ffmpeg, 2_200)
    assert any(pause.start_ms < 1_000 < pause.end_ms for pause in pauses)

    prepared = prepare_transcription_audio(
        audio,
        ffmpeg,
        tmp_path / "transcription.mp3",
        2_200,
    )
    assert prepared.suffix == ".mp3"
    assert 2_000 <= probe_audio_duration_ms(prepared, ffprobe) <= 2_400


@pytest.mark.integration
def test_full_render_and_frame_change_timing() -> None:
    ffmpeg, ffprobe = media_binaries()
    workspace = WorkspaceManager()
    try:
        definitions = [
            ("[0-02]_первый кадр.jpg", ".jpg", (255, 0, 0), (640, 360), "JPEG"),
            ("[0-05]_second frame.png", ".png", (0, 200, 0), (300, 500), "PNG"),
            ("[0-08.500]_third-frame.webp", ".webp", (0, 0, 255), (500, 300), "WEBP"),
            ("[0-12]_последний кадр.jpg", ".jpg", (240, 220, 0), (240, 240), "JPEG"),
        ]
        sources: list[SourceImage] = []
        for index, (original, suffix, color, size, image_format) in enumerate(definitions, start=1):
            stored = workspace.uploads_dir / f"{index:04d}{suffix}"
            Image.new("RGB", size, color).save(stored, format=image_format, quality=96)
            sources.append(SourceImage(original, stored))
        random.Random(42).shuffle(sources)
        items, issues = build_timeline(sources)
        assert not issues
        assert [item.end_ms for item in items] == [2_000, 5_000, 8_500, 12_000]

        audio = workspace.uploads_dir / "тестовая озвучка.wav"
        run_process([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
            "-c:a", "pcm_s16le", audio,
        ], workspace.log_dir / "test-audio.log")
        audio_duration_ms = probe_audio_duration_ms(audio, ffprobe)

        settings = RenderSettings(
            video=VideoSettings(
                width=320,
                height=240,
                fps=30,
                scale_mode="cover",
                motion_mode="none",
                transition_mode="none",
                preset="veryfast",
                crf=28,
            ),
            audio=AudioSettings(),
            end_mode="extend_last",
            keep_debug_files=False,
        )
        renderer = VideoRenderer(ffmpeg, ffprobe, workspace)
        result = renderer.render(
            items, audio, audio_duration_ms, settings,
            output_name="integration_test.mp4",
        )
        assert result.success, result.error
        assert result.output_path is not None and result.output_path.stat().st_size > 0
        info = summarize_output(result.output_path, ffprobe)
        assert info["has_video"] and info["has_audio"]
        assert info["video_codec"] == "h264"
        assert info["audio_codec"] == "aac"
        assert info["pixel_format"] == "yuv420p"
        assert info["is_cfr"]
        assert (info["width"], info["height"]) == (320, 240)
        assert abs(float(info["fps"]) - 30.0) < 0.01
        assert abs(int(info["duration_ms"]) - 12_000) <= 67
        print(f"E2E_MEDIA_INFO={info}")

        samples = [
            (1_000, "red"),
            (2_100, "green"),
            (5_100, "blue"),
            (8_600, "yellow"),
        ]
        observed_colors: dict[int, tuple[int, int, int]] = {}
        for position, (timestamp_ms, expected) in enumerate(samples, start=1):
            frame = workspace.render_dir / f"sample-{position}.png"
            run_process([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{timestamp_ms / 1000:.3f}", "-i", result.output_path,
                "-frames:v", "1", frame,
            ], workspace.log_dir / "frame-samples.log")
            with Image.open(frame) as image:
                pixel = image.convert("RGB").getpixel((160, 120))
                observed_colors[timestamp_ms] = pixel
                assert_color(pixel, expected)
        print(f"E2E_FRAME_COLORS={observed_colors}")
    finally:
        workspace.cleanup_project(keep_output=False, keep_logs=False)


@pytest.mark.integration
@pytest.mark.parametrize(("size", "fps", "preset", "crf"), [
    ((1920, 1080), 24, "veryfast", 23),
    ((1080, 1920), 25, "medium", 20),
    ((1080, 1080), 60, "slow", 18),
])
def test_real_render_video_presets(
    size: tuple[int, int],
    fps: int,
    preset: str,
    crf: int,
) -> None:
    ffmpeg, ffprobe = media_binaries()
    workspace = WorkspaceManager()
    try:
        image = workspace.uploads_dir / "preset.jpg"
        Image.new("RGB", (320, 180), (40, 80, 160)).save(image, format="JPEG")
        source = SourceImage("[0-00.200]_preset.jpg", image)
        items, issues = build_timeline([source])
        assert not issues
        audio = workspace.uploads_dir / "preset.wav"
        run_process([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=0.2",
            audio,
        ], workspace.log_dir / "preset-audio.log")
        duration = probe_audio_duration_ms(audio, ffprobe)
        settings = RenderSettings(
            VideoSettings(
                width=size[0], height=size[1], fps=fps,
                preset=preset, crf=crf, motion_mode="none", transition_mode="none",
            ),
            AudioSettings(),
            "extend_last",
        )
        result = VideoRenderer(ffmpeg, ffprobe, workspace).render(
            items, audio, duration, settings, output_name="preset.mp4"
        )
        assert result.success, result.error
        assert result.media_info["width"] == size[0]
        assert result.media_info["height"] == size[1]
        assert abs(float(result.media_info["fps"]) - fps) < 0.01
        assert result.media_info["is_cfr"]
    finally:
        workspace.cleanup_project(keep_output=False, keep_logs=False)


@pytest.mark.integration
def test_real_render_with_120_randomly_ordered_frames() -> None:
    ffmpeg, ffprobe = media_binaries()
    workspace = WorkspaceManager()
    try:
        sources: list[SourceImage] = []
        for index in range(1, 121):
            end_ms = index * 40
            seconds, millis = divmod(end_ms, 1000)
            original = f"[0-{seconds:02d}.{millis:03d}]_массовый кадр {index:03d}.png"
            stored = workspace.uploads_dir / f"bulk-{index:03d}.png"
            color = ((index * 37) % 256, (index * 67) % 256, (index * 97) % 256)
            Image.new("RGB", (64, 64), color).save(stored, format="PNG")
            sources.append(SourceImage(original, stored))
        random.Random(2026).shuffle(sources)
        items, issues = build_timeline(sources)
        assert not issues
        assert len(items) == 120
        assert items[-1].end_ms == 4_800

        audio = workspace.uploads_dir / "массовый тест.wav"
        run_process([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=4.8",
            audio,
        ], workspace.log_dir / "bulk-audio.log")
        duration = probe_audio_duration_ms(audio, ffprobe)
        settings = RenderSettings(
            VideoSettings(
                width=160, height=90, fps=30, preset="veryfast", crf=23,
                motion_mode="none", transition_mode="none",
            ),
            AudioSettings(),
            "extend_last",
        )
        result = VideoRenderer(ffmpeg, ffprobe, workspace).render(
            items, audio, duration, settings, output_name="bulk-120.mp4"
        )
        assert result.success, result.error
        assert result.output_path is not None and result.output_path.stat().st_size > 0
        assert result.media_info["has_video"] and result.media_info["has_audio"]
        assert result.media_info["is_cfr"]
        assert abs(int(result.media_info["duration_ms"]) - 4_800) <= 67
        frame_probe = subprocess.run(
            [
                str(ffprobe), "-v", "error", "-select_streams", "v:0",
                "-show_entries", "frame=pts_time", "-of", "csv=p=0",
                str(result.output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        timestamps = [
            float(value.strip().rstrip(","))
            for value in frame_probe.stdout.splitlines()
            if value.strip().rstrip(",")
        ]
        assert len(timestamps) == 144
        assert all(
            abs((current - previous) - (1 / 30)) < 0.0001
            for previous, current in zip(timestamps, timestamps[1:])
        )
    finally:
        workspace.cleanup_project(keep_output=False, keep_logs=False)
