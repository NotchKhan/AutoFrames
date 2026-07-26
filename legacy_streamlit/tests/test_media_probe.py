from __future__ import annotations

from pathlib import Path

import pytest

import services.media_probe as media_probe
from services.media_probe import MediaProbeError, check_media_tools, summarize_output


def test_missing_media_tools_have_clear_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_probe, "resolve_binary", lambda _name, _path=None: None)
    ffmpeg, ffprobe, errors = check_media_tools()
    assert ffmpeg is None and ffprobe is None
    assert any("FFmpeg не найден" in error for error in errors)
    assert any("ffprobe не найден" in error for error in errors)


def test_empty_media_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(MediaProbeError, match="отсутствует или пуст"):
        media_probe.probe_media(empty, Path("ffprobe"))


def test_output_summary_contains_stream_and_pixel_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.mp4"
    output.write_bytes(b"media")
    monkeypatch.setattr(media_probe, "probe_media", lambda *_args: {
        "format": {"duration": "12.000", "format_name": "mov,mp4"},
        "streams": [
            {
                "codec_type": "video", "codec_name": "h264", "width": 320,
                "height": 240, "pix_fmt": "yuv420p", "avg_frame_rate": "30/1",
                "r_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    })
    info = summarize_output(output, Path("ffprobe"))
    assert info["has_video"] and info["has_audio"]
    assert info["pixel_format"] == "yuv420p"
    assert info["is_cfr"]
    assert info["fps"] == 30.0
