from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from models.render import VideoSettings
from services.ffmpeg_builder import clip_command
from services.image_processor import prepare_image
from services.media_probe import check_media_tools
from services.video_renderer import choose_smart_motion_effect
from utils.process_utils import run_process


def make_image(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, "#6c63ff").save(path, format="PNG")
    return path


def test_smart_motion_uses_horizontal_pan_for_wide_source_in_vertical_video(
    tmp_path: Path,
) -> None:
    source = make_image(tmp_path / "wide.png", (1600, 600))

    assert choose_smart_motion_effect(source, 1, 3_000, 1080, 1920) == "left_right"
    assert choose_smart_motion_effect(source, 2, 3_000, 1080, 1920) == "right_left"


def test_smart_motion_uses_vertical_pan_for_tall_source_in_landscape_video(
    tmp_path: Path,
) -> None:
    source = make_image(tmp_path / "tall.png", (600, 1600))

    assert choose_smart_motion_effect(source, 1, 3_000, 1920, 1080) == "top_bottom"
    assert choose_smart_motion_effect(source, 2, 3_000, 1920, 1080) == "bottom_top"


def test_smart_motion_uses_gentle_zoom_for_matching_aspect_ratio(
    tmp_path: Path,
) -> None:
    source = make_image(tmp_path / "landscape.png", (1600, 900))

    assert choose_smart_motion_effect(source, 1, 3_000, 1920, 1080) == "zoom_in"
    assert choose_smart_motion_effect(source, 2, 3_000, 1920, 1080) == "zoom_out"


def test_smart_motion_disables_motion_for_very_short_scene(tmp_path: Path) -> None:
    source = make_image(tmp_path / "short.png", (1600, 900))

    assert choose_smart_motion_effect(source, 1, 1_199, 1920, 1080) == "none"


def test_smart_motion_uses_zoom_when_scale_mode_keeps_the_whole_image(tmp_path: Path) -> None:
    source = make_image(tmp_path / "wide.png", (1600, 600))

    assert choose_smart_motion_effect(
        source,
        1,
        3_000,
        1080,
        1920,
        "fit_blur",
    ) == "zoom_in"


def test_full_source_pan_command_crops_across_the_prepared_canvas(tmp_path: Path) -> None:
    settings = VideoSettings(width=160, height=160, fps=30, preset="veryfast")
    command = clip_command(
        Path("ffmpeg"),
        tmp_path / "prepared.png",
        tmp_path / "clip.mp4",
        settings,
        "left_right",
        45,
        1_500,
        full_source_pan=True,
    )

    filter_graph = command[command.index("-vf") + 1]
    assert "crop=w=160:h=160" in filter_graph
    assert "x='(iw-160)*n/44'" in filter_graph
    assert "zoompan" not in filter_graph


@pytest.mark.integration
def test_full_source_pan_reaches_both_sides_of_original_photo(tmp_path: Path) -> None:
    ffmpeg, _ffprobe, errors = check_media_tools()
    if ffmpeg is None:
        pytest.skip("FFmpeg unavailable: " + "; ".join(errors))

    source = tmp_path / "wide-split.png"
    image = Image.new("RGB", (480, 160), "red")
    image.paste("blue", (240, 0, 480, 160))
    image.save(source, format="PNG")

    settings = VideoSettings(
        width=160,
        height=160,
        fps=30,
        scale_mode="cover",
        motion_mode="smart",
        transition_mode="none",
        preset="veryfast",
        crf=18,
    )
    prepared = tmp_path / "prepared.png"
    assert prepare_image(
        source,
        prepared,
        settings,
        motion_axis="horizontal",
    ) == (320, 160)

    clip = tmp_path / "pan.mp4"
    run_process(
        clip_command(
            ffmpeg,
            prepared,
            clip,
            settings,
            "left_right",
            45,
            1_500,
            full_source_pan=True,
        ),
        tmp_path / "pan.log",
    )

    sampled: list[tuple[int, int, int]] = []
    for index, timestamp in enumerate((0.0, 1.4), start=1):
        frame = tmp_path / f"sample-{index}.png"
        run_process([
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            clip,
            "-frames:v",
            "1",
            frame,
        ], tmp_path / "samples.log")
        with Image.open(frame) as opened:
            sampled.append(opened.convert("RGB").getpixel((80, 80)))

    first_red, first_green, first_blue = sampled[0]
    last_red, last_green, last_blue = sampled[1]
    assert first_red > 180 and first_green < 90 and first_blue < 90
    assert last_blue > 180 and last_red < 90 and last_green < 90
