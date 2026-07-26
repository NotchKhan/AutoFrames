from __future__ import annotations

from pathlib import Path

from models.render import AudioSettings, VideoSettings
from services.ffmpeg_builder import audio_filter, clip_command, concat_command, mux_command


def test_clip_command_keeps_special_paths_as_single_arguments() -> None:
    ffmpeg = Path(r"C:\Инструменты с пробелом\ffmpeg.exe")
    image = Path(r"C:\Кадры & тест\кадр (1).png")
    output = Path(r"C:\Выход\клип 1.mp4")
    command = clip_command(
        ffmpeg, image, output, VideoSettings(width=320, height=240),
        "none", 30, 1000,
    )
    assert command[0] == str(ffmpeg)
    assert command[command.index("-i") + 1] == str(image)
    assert command[-1] == str(output)
    assert command[command.index("-fps_mode") + 1] == "cfr"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"


def test_concat_and_mux_commands_are_bounded_lists() -> None:
    ffmpeg = Path("ffmpeg.exe")
    concat = concat_command(ffmpeg, Path("concat.txt"), Path("silent.mp4"))
    mux = mux_command(
        ffmpeg, Path("silent.mp4"), Path("audio.wav"), Path("final.mp4"),
        AudioSettings(), 5000, 1000, False,
    )
    assert len(concat) < 25
    assert len(mux) < 35
    audio_input = mux.index("audio.wav")
    assert mux.index("-ss") < audio_input
    assert mux[mux.index("-c:v") + 1] == "copy"
    assert mux[mux.index("-c:a") + 1] == "aac"


def test_optional_audio_filters_do_not_change_speed() -> None:
    value = audio_filter(
        AudioSettings(normalize=True, fade_in_ms=100, fade_out_ms=200, volume_percent=80),
        target_ms=5_000,
        pad_silence=True,
    )
    assert value is not None
    assert "loudnorm" in value and "volume=0.8000" in value and "apad" in value
    assert "atempo" not in value and "asetrate" not in value
