from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from models.render import AudioSettings, RenderSettings, VideoSettings
from models.timeline import TimelineItem
from services import resource_estimator, video_renderer
from services.resource_estimator import (
    DiskEstimate,
    disk_estimate,
    estimate_required_disk_bytes,
    storage_reserve_bytes,
)
from services.video_renderer import VideoRenderer
from services.workspace_manager import WorkspaceManager


MIB = 1024 * 1024


def timeline_items(tmp_path: Path, count: int = 4) -> list[TimelineItem]:
    return [
        TimelineItem(
            index=index,
            original_filename=f"scene-{index}.png",
            stored_path=tmp_path / f"scene-{index}.png",
            parsed_timestamp="",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            duration_ms=1_000,
        )
        for index in range(1, count + 1)
    ]


def test_storage_reserve_uses_floor_on_half_gibibyte_volume() -> None:
    assert storage_reserve_bytes(
        512 * MIB,
        minimum_bytes=256 * MIB,
        maximum_bytes=512 * MIB,
        reserve_percent=10,
    ) == 256 * MIB


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (512 * MIB, 256 * MIB),
        (5 * 1024 * MIB, 512 * MIB),
        (20 * 1024 * MIB, 512 * MIB),
    ],
)
def test_storage_reserve_scales_with_filesystem_capacity_and_has_a_cap(
    total: int,
    expected: int,
) -> None:
    assert storage_reserve_bytes(
        total,
        minimum_bytes=256 * MIB,
        maximum_bytes=512 * MIB,
        reserve_percent=10,
    ) == expected


def test_tiny_render_is_allowed_with_railway_trial_free_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free = round(423.3 * MIB)
    monkeypatch.setattr(
        resource_estimator.shutil,
        "disk_usage",
        lambda _directory: SimpleNamespace(total=512 * MIB, free=free),
    )

    estimate = disk_estimate(
        tmp_path,
        timeline_items(tmp_path),
        VideoSettings(
            width=640,
            height=360,
            fps=30,
            scale_mode="cover",
            motion_mode="smart",
            crf=20,
        ),
        4_500,
    )

    assert estimate.reserve_bytes == 64 * MIB
    assert estimate.required_bytes == 103_176_470
    assert estimate.sufficient


def test_disk_estimate_keeps_only_one_prepared_frame_in_peak_budget(tmp_path: Path) -> None:
    settings = VideoSettings(
        width=640,
        height=360,
        fps=30,
        scale_mode="cover",
        motion_mode="smart",
        crf=20,
    )

    one_frame = estimate_required_disk_bytes(
        timeline_items(tmp_path, count=1),
        settings,
        4_500,
        reserve_bytes=256 * MIB,
    )
    many_frames = estimate_required_disk_bytes(
        timeline_items(tmp_path, count=141),
        settings,
        4_500,
        reserve_bytes=256 * MIB,
    )

    assert many_frames == one_frame


def test_eight_minute_1080p_project_fits_one_gibibyte_scratch_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_estimator.shutil,
        "disk_usage",
        lambda _directory: SimpleNamespace(total=1024 * MIB, free=900 * MIB),
    )

    estimate = disk_estimate(
        tmp_path,
        timeline_items(tmp_path, count=146),
        VideoSettings(width=1920, height=1080, fps=30, crf=20, motion_mode="none"),
        480_000,
    )

    assert estimate.reserve_bytes == (1024 * MIB * 10 + 99) // 100
    assert estimate.required_bytes < 850 * MIB
    assert estimate.sufficient


def test_debug_artifacts_require_more_scratch_space(tmp_path: Path) -> None:
    items = timeline_items(tmp_path, count=146)
    settings = VideoSettings(width=1920, height=1080, fps=30, crf=20)

    normal = estimate_required_disk_bytes(items, settings, 480_000)
    debug = estimate_required_disk_bytes(
        items,
        settings,
        480_000,
        keep_debug_files=True,
    )

    assert debug > normal


def test_guard_still_rejects_render_that_would_consume_protected_reserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free = 90 * MIB
    monkeypatch.setattr(
        resource_estimator.shutil,
        "disk_usage",
        lambda _directory: SimpleNamespace(total=512 * MIB, free=free),
    )

    estimate = disk_estimate(
        tmp_path,
        timeline_items(tmp_path),
        VideoSettings(width=640, height=360, fps=30, crf=20),
        4_500,
    )

    assert estimate.reserve_bytes == 64 * MIB
    assert estimate.required_bytes > free
    assert not estimate.sufficient


def test_config_rejects_explicit_scratch_reserve_floor_below_32_mib() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": str(backend_root / "app"),
        "SCRATCH_RESERVE_MIN_MB": "31",
        "SCRATCH_RESERVE_MAX_MB": "128",
        "STORAGE_RESERVE_PERCENT": "10",
    })

    result = subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert "SCRATCH_RESERVE_MIN_MB" in result.stderr
    assert "32" in result.stderr


def test_renderer_clears_owned_intermediates_before_disk_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceManager()
    try:
        source = workspace.uploads_dir / "scene.png"
        audio = workspace.uploads_dir / "audio.wav"
        source.write_bytes(b"image-placeholder")
        audio.write_bytes(b"audio-placeholder")
        stale_files = [
            workspace.prepared_dir / "stale.png",
            workspace.clips_dir / "stale.mp4",
            workspace.render_dir / "stale.partial.mp4",
        ]
        for stale in stale_files:
            stale.write_bytes(b"stale")

        observed = False

        def insufficient_after_cleanup(
            _directory: Path,
            _items: list[TimelineItem],
            _settings: VideoSettings,
            _duration_ms: int,
            **_kwargs: object,
        ) -> DiskEstimate:
            nonlocal observed
            observed = True
            assert all(not stale.exists() for stale in stale_files)
            return DiskEstimate(required_bytes=2, free_bytes=1, reserve_bytes=1)

        monkeypatch.setattr(video_renderer, "disk_estimate", insufficient_after_cleanup)
        item = TimelineItem(
            index=1,
            original_filename="scene.png",
            stored_path=source,
            parsed_timestamp="",
            start_ms=0,
            end_ms=1_000,
            duration_ms=1_000,
        )
        result = VideoRenderer(Path("ffmpeg"), Path("ffprobe"), workspace).render(
            [item],
            audio,
            1_000,
            RenderSettings(
                VideoSettings(width=640, height=360),
                AudioSettings(),
                "extend_last",
            ),
        )

        assert observed
        assert not result.success
        assert result.error and "Недостаточно свободного места" in result.error
    finally:
        workspace.cleanup_project(keep_output=False, keep_logs=False)


def test_renderer_prepares_one_frame_at_a_time_and_releases_consumed_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceManager()
    try:
        items = timeline_items(workspace.uploads_dir, count=2)
        for index, item in enumerate(items, start=1):
            Image.new("RGB", (320, 180), (index * 50, 80, 120)).save(
                item.stored_path,
                format="PNG",
            )
        audio = workspace.uploads_dir / "audio.wav"
        audio.write_bytes(b"audio-placeholder")
        monkeypatch.setattr(
            video_renderer,
            "disk_estimate",
            lambda *_args, **_kwargs: DiskEstimate(
                required_bytes=1,
                free_bytes=2,
                reserve_bytes=0,
            ),
        )

        prepared_seen: list[str] = []

        def fake_run_process(
            command: list[str],
            _log_path: Path,
            _cancel: object | None = None,
            **_kwargs: object,
        ) -> None:
            output = Path(command[-1])
            if output.parent == workspace.clips_dir and output.suffix == ".mp4":
                prepared = list(workspace.prepared_dir.glob("*.png"))
                assert len(prepared) == 1
                assert Path(command[command.index("-i") + 1]) == prepared[0]
                prepared_seen.append(prepared[0].name)
            elif output.name == "silent_video.mp4":
                assert list(workspace.prepared_dir.iterdir()) == []
                assert len(list(workspace.clips_dir.glob("*.mp4"))) == 2
            elif output.name.endswith(".partial.mp4"):
                assert list(workspace.prepared_dir.iterdir()) == []
                assert list(workspace.clips_dir.iterdir()) == []
                assert (workspace.render_dir / "silent_video.mp4").exists()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake-media")

        monkeypatch.setattr(video_renderer, "run_process", fake_run_process)
        monkeypatch.setattr(
            video_renderer,
            "summarize_output",
            lambda *_args: {
                "duration_ms": 2_000,
                "has_video": True,
                "has_audio": True,
                "video_codec": "h264",
                "audio_codec": "aac",
                "pixel_format": "yuv420p",
                "is_cfr": True,
                "container": "mp4",
                "width": 160,
                "height": 90,
                "fps": 30.0,
            },
        )

        result = VideoRenderer(Path("ffmpeg"), Path("ffprobe"), workspace).render(
            items,
            audio,
            2_000,
            RenderSettings(
                VideoSettings(
                    width=160,
                    height=90,
                    fps=30,
                    preset="veryfast",
                    motion_mode="none",
                    transition_mode="none",
                ),
                AudioSettings(),
                "extend_last",
            ),
            output_name="jit-prepared.mp4",
        )

        assert result.success, result.error
        assert prepared_seen == ["000001.png", "000002.png"]
        assert list(workspace.prepared_dir.iterdir()) == []
    finally:
        workspace.cleanup_project(keep_output=False, keep_logs=False)
