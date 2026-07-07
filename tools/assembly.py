"""Final FFmpeg assembly for MinecraftCast.

Composites each segment — darkened Minecraft footage as the background, the two
avatar clips overlaid in the bottom corners (the speaker in full color, the
listener desaturated), and a subtitle bar — then concatenates all segments and
mixes in low-volume background music.

Layout (1920x1080):
    ┌─────────────────────────────────────────────┐
    │            Minecraft footage (darkened)       │
    │  ┌────────┐                     ┌────────┐    │
    │  │ CHAR 1 │                     │ CHAR 2 │    │
    │  │300x380 │                     │300x380 │    │
    │  └────────┘                     └────────┘    │
    │  x=40,y=640                     x=1580,y=640  │
    │  ═══════════════════════════════════════════  │
    │  [NAME]: dialogue text                        │
    └─────────────────────────────────────────────┘

All FFmpeg calls go through subprocess (no ffmpeg-python wrapper).
"""

import os
import json
import subprocess

from config import VideoConfig


def _escape_drawtext(text: str) -> str:
    """Escape a string for safe use inside an FFmpeg drawtext ``text=`` value."""
    # Order matters: backslash first, then the characters FFmpeg treats specially.
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "’")   # curly apostrophe dodges quote parsing
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    return text


def _probe_duration(audio_path: str) -> float:
    """Return the duration in seconds of an audio file via ffprobe."""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", audio_path],
        capture_output=True, text=True,
    )
    probe_data = json.loads(probe.stdout)
    return float(probe_data["streams"][0]["duration"])


# Desaturation matrix applied to the non-speaking avatar.
_DESAT = "colorchannelmixer=.4:.2:.2:0:.2:.4:.2:0:.2:.2:.4"


def assemble_segment(seg_idx: int, seg: dict,
                     footage_path: str,
                     audio_path: str,
                     char1_avatar_path: str,
                     char2_avatar_path: str,
                     config: VideoConfig,
                     job_id: str) -> str:
    """Assemble one segment clip. Returns the segment MP4 path."""
    output_path = f"/tmp/minecraftcast/{job_id}/segments/seg_{seg_idx:03d}.mp4"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    duration = _probe_duration(audio_path)

    # Spread the footage window across the source to avoid visible repetition.
    footage_offset = (seg_idx * 30) % 600  # stay within the first 10 minutes

    # Subtitle text: "Speaker: line".
    speaker_label = {
        "char1": config.char1.name,
        "char2": config.char2.name,
        "narrator": "Narrator",
    }[seg["speaker"]]
    subtitle_text = _escape_drawtext(f"{speaker_label}: {seg['text']}"[:120])

    char1_is_speaking = seg["speaker"] == "char1"

    # Build the complex filter graph.
    # Inputs: 0=footage, 1=char1 avatar, 2=char2 avatar, 3=audio.
    filter_complex = (
        # Trim + scale footage to 1920x1080, then darken it.
        f"[0:v]trim=start={footage_offset}:duration={duration},"
        f"setpts=PTS-STARTPTS,"
        f"scale=1920:1080:force_original_aspect_ratio=increase,"
        f"crop=1920:1080,"
        f"colorchannelmixer=.6:.6:.6:0:.6:.6:.6:0:.6:.6:.6[bg];"
        # Scale avatar inputs.
        f"[1:v]scale=300:380[char1v];"
        f"[2:v]scale=300:380[char2v];"
    )

    # Desaturate the non-speaking avatar, then overlay both.
    if char1_is_speaking:
        filter_complex += f"[char2v]{_DESAT}[char2vd];"
        filter_complex += (
            f"[bg][char1v]overlay=x=40:y=640[tmp1];"
            f"[tmp1][char2vd]overlay=x=1580:y=640[tmp2];"
        )
    else:
        filter_complex += f"[char1v]{_DESAT}[char1vd];"
        filter_complex += (
            f"[bg][char1vd]overlay=x=40:y=640[tmp1];"
            f"[tmp1][char2v]overlay=x=1580:y=640[tmp2];"
        )

    # Subtitle bar via drawtext.
    filter_complex += (
        f"[tmp2]drawtext="
        f"text='{subtitle_text}':"
        f"fontcolor=white:"
        f"fontsize=28:"
        f"bordercolor=black:"
        f"borderw=3:"
        f"x=(w-text_w)/2:"
        f"y=h-60:"
        f"box=1:"
        f"boxcolor=black@0.5:"
        f"boxborderw=8[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", footage_path,       # input 0: footage
        "-i", char1_avatar_path,  # input 1: char1 avatar video
        "-i", char2_avatar_path,  # input 2: char2 avatar video
        "-i", audio_path,         # input 3: dialogue audio
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "3:a",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "medium",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def concatenate_segments(segment_paths: list, music_path: str,
                         total_duration: float, job_id: str,
                         config: VideoConfig) -> str:
    """Concatenate all segment clips and mix in background music.

    Returns the path to the final MP4 under ``output/``.
    """
    concat_list = f"/tmp/minecraftcast/{job_id}/concat_list.txt"
    with open(concat_list, "w") as f:
        for path in segment_paths:
            f.write(f"file '{path}'\n")

    output_path = f"output/{job_id}_final.mp4"
    os.makedirs("output", exist_ok=True)

    fade_out_start = max(0.0, total_duration - 3)

    if music_path and os.path.exists(music_path):
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-i", music_path,
            "-filter_complex",
            f"[1:a]volume=0.08,afade=t=in:d=2,"
            f"afade=t=out:st={fade_out_start}:d=3[music];"
            f"[0:a][music]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "libx264", "-crf", "23", "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
