"""FFmpeg assembly pipeline (subprocess-based for reliability).

Each scene is rendered to a normalized, self-contained MP4 (matching resolution,
fps, codecs) with: source scaling/padding or Ken Burns, color grade, burned-in
captions, optional text overlay, and muxed narration audio. Scenes are then
concatenated and a background-music bed is mixed under the narration.
"""

import os
import json
import asyncio
import logging
import subprocess

from tools import script as script_engine

log = logging.getLogger("videoforge.assembly")

FPS = 30
SCENE_CRF = "23"
PRESET = "medium"


def _out_dir(job_id: str) -> str:
    d = f"/tmp/videoforge/{job_id}/out"
    os.makedirs(d, exist_ok=True)
    return d


async def build(job_id, media, audio, srt, music_path, script) -> str:
    """Assemble the final MP4. Runs the blocking FFmpeg work in a thread."""
    return await asyncio.to_thread(
        assemble_video, job_id, media, audio, srt, music_path, script
    )


def assemble_video(job_id, media, audio, srt, music_path, script) -> str:
    out_dir = _out_dir(job_id)
    resolution = script.get("target_resolution", "1920x1080")
    width, height = (int(x) for x in resolution.split("x"))
    is_shorts = script.get("style") == "shorts" or height > width

    scenes = [s for ch in script.get("chapters", []) for s in ch.get("scenes", [])]

    scene_files: list[str] = []
    total_duration = 0.0

    for scene in scenes:
        idx = scene["scene_index"]
        media_path = media.get(idx)
        audio_path = audio.get(idx)
        srt_path = srt.get(idx)

        if not media_path or not audio_path:
            log.warning("Scene %s missing media/audio; skipping", idx)
            continue

        dur = _audio_duration(audio_path)
        total_duration += dur

        scene_out = os.path.join(out_dir, f"scene_{idx}_final.mp4")
        _render_scene(
            scene, media_path, audio_path, srt_path, scene_out,
            width, height, dur, is_shorts,
        )
        scene_files.append(scene_out)

    if not scene_files:
        raise RuntimeError("No scenes were rendered; cannot assemble video")

    final_path = os.path.join(out_dir, "output.mp4")
    _concat_with_music(scene_files, music_path, final_path, total_duration, out_dir)
    log.info("Assembled final video: %s (%.1fs)", final_path, total_duration)
    return final_path


# ---------------------------------------------------------------------------
# Per-scene rendering
# ---------------------------------------------------------------------------

def _render_scene(scene, media_path, audio_path, srt_path, out_path,
                  width, height, duration, is_shorts) -> None:
    ext = os.path.splitext(media_path)[1].lower()
    is_image = ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp")

    vf = _build_filter_chain(scene, srt_path, width, height, duration,
                             is_shorts, is_image)

    cmd = ["ffmpeg", "-y"]
    if is_image:
        cmd += ["-loop", "1", "-t", f"{duration:.3f}", "-i", media_path]
    else:
        # Loop short clips so they always cover the narration duration.
        cmd += ["-stream_loop", "-1", "-t", f"{duration:.3f}", "-i", media_path]
    cmd += ["-i", audio_path]

    cmd += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-r", str(FPS),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-crf", SCENE_CRF, "-preset", PRESET,
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-shortest",
        out_path,
    ]
    _run(cmd, f"render scene {scene.get('scene_index')}")


def _build_filter_chain(scene, srt_path, width, height, duration,
                        is_shorts, is_image) -> str:
    parts: list[str] = []

    if is_image and is_shorts:
        # Ken Burns: oversize then slow zoom over the scene duration.
        frames = max(1, int(duration * FPS))
        parts.append(
            f"scale={int(width * 1.3)}:{int(height * 1.3)}:"
            f"force_original_aspect_ratio=increase"
        )
        parts.append(
            f"zoompan=z='min(zoom+0.0012,1.4)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={FPS}"
        )
    else:
        parts.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease"
        )
        parts.append(
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )
    parts.append("setsar=1")

    # Color grade.
    grade = script_engine.get_ffmpeg_color_grade(scene.get("color_grade", "neutral"))
    if grade:
        parts.append(grade)

    # Burn captions.
    if srt_path and os.path.exists(srt_path):
        font_size = 28 if is_shorts else 18
        style = (
            f"FontSize={font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            "Outline=2,Shadow=0,BorderStyle=1,Alignment=2,MarginV=40"
        )
        parts.append(f"subtitles='{_escape_path(srt_path)}':force_style='{style}'")

    # Optional text overlay (top-third by default).
    overlay = scene.get("text_overlay")
    if overlay:
        pos = scene.get("text_overlay_position") or "top"
        y = {"top": "h/8", "middle": "(h-text_h)/2", "bottom": "h-h/6"}.get(pos, "h/8")
        font = _find_font()
        font_clause = f"fontfile='{_escape_path(font)}':" if font else ""
        size = 56 if is_shorts else 40
        parts.append(
            f"drawtext={font_clause}text='{_escape_text(overlay)}':"
            f"fontcolor=white:fontsize={size}:borderw=3:bordercolor=black@0.8:"
            f"x=(w-text_w)/2:y={y}"
        )

    return ",".join(parts)


# ---------------------------------------------------------------------------
# Concat + music mix
# ---------------------------------------------------------------------------

def _concat_with_music(scene_files, music_path, final_path, total, out_dir) -> None:
    list_path = os.path.join(out_dir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in scene_files:
            f.write(f"file '{os.path.abspath(p)}'\n")

    fade_out_start = max(total - 3.0, 0.0)
    filter_complex = (
        f"[1:a]volume=0.08,afade=t=in:d=2,"
        f"afade=t=out:st={fade_out_start:.3f}:d=3[bg];"
        f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-i", music_path,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-crf", "23", "-preset", PRESET,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        final_path,
    ]
    _run(cmd, "concat + music mix")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audio_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path],
            check=True, capture_output=True,
        )
        return max(0.5, float(json.loads(out.stdout)["format"]["duration"]))
    except Exception as e:
        log.warning("ffprobe failed for %s (%s); defaulting to 5s", path, e)
        return 5.0


def _run(cmd: list[str], label: str) -> None:
    log.debug("FFmpeg [%s]: %s", label, " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "ignore")[-1500:]
        raise RuntimeError(f"FFmpeg failed during {label}:\n{err}")


def _escape_path(path: str) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument."""
    return path.replace("\\", "/").replace(":", "\\:")


def _escape_text(text: str) -> str:
    """Escape text for the drawtext filter."""
    text = text[:120]
    return (text.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "’")
                .replace("%", "\\%"))


def _find_font() -> str | None:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if os.path.exists(p):
            return p
    return None
