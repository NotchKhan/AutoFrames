from __future__ import annotations

from pathlib import Path

import pytest

import services.media_probe as media_probe_module
from services.media_probe import summarize_output


def _probe_payload(*, real_rate: str, average_rate: str) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "r_frame_rate": real_rate,
                "avg_frame_rate": average_rate,
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "7.000000", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }


def test_cfr_accepts_one_tick_mp4_duration_rounding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "short-concat.mp4"
    output.write_bytes(b"mp4")
    monkeypatch.setattr(
        media_probe_module,
        "probe_media",
        lambda _path, _ffprobe: _probe_payload(
            real_rate="30/1",
            average_rate="630000/20999",
        ),
    )

    info = summarize_output(output, Path("ffprobe"))

    assert info["is_cfr"] is True


def test_cfr_still_rejects_fractional_rate_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "vfr.mp4"
    output.write_bytes(b"mp4")
    monkeypatch.setattr(
        media_probe_module,
        "probe_media",
        lambda _path, _ffprobe: _probe_payload(
            real_rate="30/1",
            average_rate="30000/1001",
        ),
    )

    info = summarize_output(output, Path("ffprobe"))

    assert info["is_cfr"] is False
