"""Core MinecraftCast pipeline — fully standalone, zero CROO dependency.

Given a :class:`VideoConfig`, this runs the whole flow end to end and returns the
path to a finished MP4 under ``output/``:

    1. Script     — Groq dialogue generation
    2. Footage    — YouTube / Archive.org / uploaded clip
    3. Voice setup — resolve/clone voices for both characters
    4. Audio      — TTS every dialogue segment
    5. Avatars    — render animated per-segment avatar clips
    6. Assembly   — composite segments, concat, mix music

Nothing in this module imports anything from ``integrations/``; the CROO layer
(and REST/MCP adapters) wrap *this* function, never the other way around.
"""

import os
import sys
import shutil

from config import VideoConfig
from tools import script as script_tool
from tools import footage as footage_tool
from tools import voice_router
from tools import music as music_tool
from tools import assembly as assembly_tool
from tools.avatar import AvatarGenerator


def _force_utf8_console() -> None:
    """Make stdout/stderr UTF-8 so the progress banners (═ ╔ ✓) never crash.

    The Windows console defaults to a legacy code page (cp1252) that can't encode
    the box-drawing / check characters we print. Reconfiguring to UTF-8 with
    ``errors="replace"`` keeps output readable on every platform. No-op where the
    streams don't support reconfiguration.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — best effort; never fatal
            pass


async def run(config: VideoConfig) -> str:
    """Run the full MinecraftCast pipeline. Returns the final MP4 path."""
    _force_utf8_console()
    job_id = config.job_id
    os.makedirs(f"/tmp/minecraftcast/{job_id}", exist_ok=True)

    print(f"\n{'═' * 50}")
    print(f"  JOB: {job_id}")
    print(f"{'═' * 50}")

    # 1. Script
    print("\n[1/6] Generating script...")
    script = await script_tool.generate_script(config)
    total_duration = script["total_duration_seconds"]
    print(f"      ✓ {len(script['segments'])} segments · {total_duration}s · \"{script['title']}\"")

    # 2. Footage
    print("\n[2/6] Fetching Minecraft footage...")
    footage_path = await footage_tool.fetch_footage(config, job_id)
    print(f"      ✓ {footage_path}")

    # 3. Voice setup
    print("\n[3/6] Setting up voices...")
    voice_engine = voice_router.get_voice_engine(config.char1.voice_provider)
    char1_speaker = await voice_engine.setup_voice(config.char1)
    char2_speaker = await voice_engine.setup_voice(config.char2)
    print(f"      ✓ {config.char1.name}: {char1_speaker}")
    print(f"      ✓ {config.char2.name}: {char2_speaker}")

    # 4. Audio generation
    print("\n[4/6] Generating dialogue audio...")
    audio_segments = await voice_engine.generate_all(
        script["segments"], char1_speaker, char2_speaker, job_id
    )
    print(f"      ✓ {len(audio_segments)} audio clips")

    # 5. Avatar rendering
    print("\n[5/6] Rendering avatars...")
    avatar_gen = AvatarGenerator()
    avatar_gen.create_avatar_states(config.char1, job_id)
    avatar_gen.create_avatar_states(config.char2, job_id)

    avatar_videos: dict = {}
    for seg in script["segments"]:
        idx = seg["segment_index"]
        dur = seg["duration_seconds"]
        char1_talking = seg["speaker"] == "char1"
        char2_talking = seg["speaker"] == "char2"

        avatar_videos[idx] = {
            "char1": avatar_gen.create_segment_video(
                config.char1, char1_talking, dur, job_id, idx, "char1"
            ),
            "char2": avatar_gen.create_segment_video(
                config.char2, char2_talking, dur, job_id, idx, "char2"
            ),
        }
    print("      ✓ Avatar animations done")

    # 6. Assembly
    print("\n[6/6] Assembling final video...")
    segment_paths: list[str] = []
    for seg in script["segments"]:
        idx = seg["segment_index"]
        seg_path = assembly_tool.assemble_segment(
            idx, seg,
            footage_path,
            audio_segments[idx],
            avatar_videos[idx]["char1"],
            avatar_videos[idx]["char2"],
            config, job_id,
        )
        segment_paths.append(seg_path)
        print(f"      · Segment {idx + 1}/{len(script['segments'])} done", end="\r")

    music_path = await music_tool.fetch(script["background_music_mood"], job_id)
    final_path = assembly_tool.concatenate_segments(
        segment_paths, music_path, total_duration, job_id, config
    )
    print(f"\n      ✓ {final_path}")

    # Cleanup temp working files.
    shutil.rmtree(f"/tmp/minecraftcast/{job_id}", ignore_errors=True)

    return final_path
