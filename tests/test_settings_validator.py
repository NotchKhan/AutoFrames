from __future__ import annotations

import pytest

from models.render import AudioSettings, RenderSettings, VideoSettings
from services.settings_validator import validate_render_settings, validate_video_settings


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1080, 1080)])
@pytest.mark.parametrize("fps", [24, 25, 30, 60])
def test_supported_video_parameters(size: tuple[int, int], fps: int) -> None:
    assert not validate_video_settings(VideoSettings(width=size[0], height=size[1], fps=fps))


def test_invalid_video_and_audio_parameters_are_rejected() -> None:
    settings = RenderSettings(
        VideoSettings(width=1919, height=0, fps=29, crf=99),
        AudioSettings(volume_percent=250, fade_in_ms=-1),
        "extend_last",
    )
    messages = [issue.message for issue in validate_render_settings(settings)]
    assert any("положительными" in message for message in messages)
    assert any("чётными" in message for message in messages)
    assert any("FPS" in message for message in messages)
    assert any("Громкость" in message for message in messages)

