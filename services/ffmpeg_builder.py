from __future__ import annotations

from pathlib import Path

from models.render import AudioSettings, VideoSettings
from utils.time_utils import ms_to_ffmpeg_time


def video_filter(settings: VideoSettings, effect: str, frames: int, duration_ms: int) -> str:
    filters: list[str] = []
    last_frame = max(frames - 1, 1)
    strength = max(0.001, min(settings.motion_strength * settings.motion_speed, 0.35))
    progress = f"on/{last_frame}"
    if effect == "zoom_in":
        zoom = f"1+{strength:.6f}*{progress}"
        filters.append(
            f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d=1:s={settings.width}x{settings.height}:fps={settings.fps}"
        )
    elif effect == "zoom_out":
        zoom = f"1+{strength:.6f}*(1-{progress})"
        filters.append(
            f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d=1:s={settings.width}x{settings.height}:fps={settings.fps}"
        )
    elif effect in {"left_right", "right_left", "top_bottom", "bottom_top"}:
        zoom = 1.0 + max(strength, 0.03)
        x = "(iw-iw/zoom)*on/{0}".format(last_frame)
        y = "(ih-ih/zoom)/2"
        if effect == "right_left":
            x = "(iw-iw/zoom)*(1-on/{0})".format(last_frame)
        elif effect == "top_bottom":
            x, y = "(iw-iw/zoom)/2", "(ih-ih/zoom)*on/{0}".format(last_frame)
        elif effect == "bottom_top":
            x, y = "(iw-iw/zoom)/2", "(ih-ih/zoom)*(1-on/{0})".format(last_frame)
        filters.append(
            f"zoompan=z='{zoom:.6f}':x='{x}':y='{y}':d=1:"
            f"s={settings.width}x{settings.height}:fps={settings.fps}"
        )
    else:
        filters.append(f"fps={settings.fps}")

    if settings.transition_mode != "none" and settings.transition_duration_ms > 0:
        transition_ms = min(settings.transition_duration_ms, max(duration_ms // 2, 0))
        if transition_ms > 0:
            transition = transition_ms / 1000
            duration = duration_ms / 1000
            out_start = max(0.0, duration - transition)
            # Безопасный fade внутри границ кадра: длительность таймлайна не меняется.
            filters.extend([
                f"fade=t=in:st=0:d={transition:.3f}",
                f"fade=t=out:st={out_start:.3f}:d={transition:.3f}",
            ])
    filters.append("format=yuv420p")
    return ",".join(filters)


def clip_command(
    ffmpeg: Path,
    image: Path,
    output: Path,
    settings: VideoSettings,
    effect: str,
    frames: int,
    duration_ms: int,
) -> list[str]:
    return [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "warning",
        "-loop", "1", "-framerate", str(settings.fps), "-i", str(image),
        "-vf", video_filter(settings, effect, frames, duration_ms),
        "-frames:v", str(frames), "-an", "-c:v", "libx264",
        "-preset", settings.preset, "-crf", str(settings.crf),
        "-pix_fmt", "yuv420p", "-r", str(settings.fps),
        "-video_track_timescale", "90000", str(output),
    ]


def concat_command(ffmpeg: Path, concat_file: Path, output: Path) -> list[str]:
    return [
        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-map", "0:v:0", "-c:v", "copy", "-an", "-movflags", "+faststart", str(output),
    ]


def audio_filter(settings: AudioSettings, target_ms: int, pad_silence: bool) -> str | None:
    filters: list[str] = []
    if settings.volume_percent != 100:
        filters.append(f"volume={settings.volume_percent / 100:.4f}")
    if settings.normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if settings.fade_in_ms > 0:
        filters.append(f"afade=t=in:st=0:d={settings.fade_in_ms / 1000:.3f}")
    if settings.fade_out_ms > 0:
        duration = min(settings.fade_out_ms, target_ms) / 1000
        start = max(0, target_ms - settings.fade_out_ms) / 1000
        filters.append(f"afade=t=out:st={start:.3f}:d={duration:.3f}")
    if pad_silence:
        filters.append("apad")
    return ",".join(filters) if filters else None


def mux_command(
    ffmpeg: Path,
    video: Path,
    audio: Path,
    output: Path,
    audio_settings: AudioSettings,
    target_ms: int,
    audio_offset_ms: int,
    pad_silence: bool,
) -> list[str]:
    command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "warning", "-i", str(video)]
    if audio_offset_ms > 0:
        command.extend(["-ss", ms_to_ffmpeg_time(audio_offset_ms)])
    command.extend(["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"])
    afilter = audio_filter(audio_settings, target_ms, pad_silence)
    if afilter:
        command.extend(["-af", afilter])
    command.extend([
        "-t", ms_to_ffmpeg_time(target_ms), "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k", "-movflags", "+faststart", str(output),
    ])
    return command
