from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.render import AudioSettings, RenderSettings, VideoSettings
from models.timeline import TimelineItem
from services import resource_estimator, video_renderer
from services.resource_estimator import DiskEstimate, disk_estimate, storage_reserve_bytes
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

    assert estimate.reserve_bytes == 256 * MIB
    assert estimate.required_bytes == 343_107_488
    assert estimate.sufficient


def test_guard_still_rejects_render_that_would_consume_protected_reserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free = 180 * MIB
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

    assert estimate.reserve_bytes == 256 * MIB
    assert estimate.required_bytes > free
    assert not estimate.sufficient


def test_config_rejects_explicit_reserve_floor_below_128_mib() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": str(backend_root / "app"),
        "STORAGE_RESERVE_MIN_MB": "127",
        "STORAGE_RESERVE_MAX_MB": "512",
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
    assert "STORAGE_RESERVE_MIN_MB" in result.stderr
    assert "128" in result.stderr


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
