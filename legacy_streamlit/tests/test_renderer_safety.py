from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import services.workspace_manager as workspace_module
import services.video_renderer as renderer_module
from models.render import AudioSettings, RenderSettings, VideoSettings
from models.timeline import TimelineItem
from services.video_renderer import VideoRenderer
from services.workspace_manager import WorkspaceManager
from utils.process_utils import ProcessExecutionError


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkspaceManager:
    monkeypatch.setattr(workspace_module, "TEMP_ROOT", tmp_path / "temp")
    monkeypatch.setattr(workspace_module, "OUTPUT_ROOT", tmp_path / "output")
    monkeypatch.setattr(workspace_module, "LOG_ROOT", tmp_path / "logs")
    return WorkspaceManager("e" * 32)


def valid_inputs(workspace: WorkspaceManager) -> tuple[list[TimelineItem], Path]:
    image = workspace.uploads_dir / "image.jpg"
    Image.new("RGB", (64, 64), "red").save(image, format="JPEG")
    audio = workspace.uploads_dir / "audio.wav"
    audio.write_bytes(b"audio-placeholder")
    item = TimelineItem(1, "[0-01]_кадр.jpg", image, "[0-01]", 0, 1_000, 1_000)
    return [item], audio


def settings() -> RenderSettings:
    return RenderSettings(
        VideoSettings(width=320, height=240, fps=30, preset="veryfast", crf=28),
        AudioSettings(),
        "extend_last",
    )


def test_renderer_rejects_output_path_traversal_before_process_start(
    workspace: WorkspaceManager,
) -> None:
    items, audio = valid_inputs(workspace)
    renderer = VideoRenderer(Path("ffmpeg.exe"), Path("ffprobe.exe"), workspace)
    result = renderer.render(items, audio, 1_000, settings(), output_name="..\\escape.mp4")
    assert not result.success
    assert "имя результата" in (result.error or "")
    assert not (workspace.root.parent / "escape.mp4").exists()


def test_intermediates_are_cleaned_after_ffmpeg_error(
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items, audio = valid_inputs(workspace)

    def fail_process(*_args: object, **_kwargs: object) -> None:
        raise ProcessExecutionError("ffmpeg", 1, workspace.log_dir / "ffmpeg.log")

    monkeypatch.setattr(renderer_module, "run_process", fail_process)
    renderer = VideoRenderer(Path("ffmpeg.exe"), Path("ffprobe.exe"), workspace)
    result = renderer.render(items, audio, 1_000, settings(), output_name="test.mp4")
    assert not result.success
    assert list(workspace.prepared_dir.iterdir()) == []
    assert list(workspace.clips_dir.iterdir()) == []
    assert list(workspace.render_dir.iterdir()) == []

