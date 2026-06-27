"""Kokoro TTS narration generator (local, no network).

Kokoro pipelines are expensive to construct, so they are created exactly once
and cached at module scope. `warmup()` forces that construction at process
startup; otherwise the first job pays the load cost lazily.

Each scene's narration is synthesized to a WAV (24 kHz) then transcoded to MP3
via an FFmpeg subprocess.
"""

import os
import wave
import asyncio
import logging
import subprocess

import numpy as np

from tools import script as script_engine

log = logging.getLogger("videoforge.voice")

SAMPLE_RATE = 24000  # Kokoro output sample rate

# Module-level singletons (loaded once, reused across all jobs).
_pipeline_female = None
_pipeline_male = None

# Map every voice id to the pipeline language it needs ('a' = American English).
_FEMALE_VOICES = {"af_heart", "af_sky"}
_MALE_VOICES = {"am_echo"}


def warmup() -> None:
    """Eagerly construct both Kokoro pipelines. Call once at startup."""
    _get_pipeline("af_heart")
    _get_pipeline("am_echo")
    log.info("Kokoro pipelines ready.")


def _get_pipeline(voice: str):
    """Return the cached KPipeline appropriate for `voice`, constructing it on
    first use."""
    global _pipeline_female, _pipeline_male
    from kokoro import KPipeline  # imported lazily to keep module import cheap

    if voice in _MALE_VOICES:
        if _pipeline_male is None:
            log.info("Loading Kokoro male pipeline...")
            _pipeline_male = KPipeline(lang_code="a")
        return _pipeline_male

    if _pipeline_female is None:
        log.info("Loading Kokoro female pipeline...")
        _pipeline_female = KPipeline(lang_code="a")
    return _pipeline_female


def _audio_dir(job_id: str) -> str:
    d = f"/tmp/videoforge/{job_id}/audio"
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_all(script: dict, job_id: str) -> dict:
    """Generate narration MP3s for every scene. Returns
    {scene_index: mp3_path}. Kokoro runs CPU/GPU-bound so each scene is
    offloaded to a thread; scenes run sequentially to avoid model contention."""
    voice = script_engine.get_voice_for_style(script.get("style", "faceless"))
    scenes = [s for ch in script.get("chapters", []) for s in ch.get("scenes", [])]

    out: dict[int, str] = {}
    for scene in scenes:
        idx = scene["scene_index"]
        narration = str(scene.get("narration", "")).strip() or scene.get("title", "")
        path = await asyncio.to_thread(
            generate_scene_voice, narration, voice, idx, job_id
        )
        out[idx] = path
    return out


# Spec alias.
generate_all_voice = generate_all


def generate_scene_voice(narration: str, voice: str, scene_index: int, job_id: str) -> str:
    """Synthesize one scene's narration to MP3 and return its path. Runs in a
    worker thread (blocking)."""
    audio_dir = _audio_dir(job_id)
    wav_path = os.path.join(audio_dir, f"scene_{scene_index}.wav")
    mp3_path = os.path.join(audio_dir, f"scene_{scene_index}.mp3")

    pipeline = _get_pipeline(voice)

    chunks: list[np.ndarray] = []
    text = narration if narration.strip() else "."
    for _, _, audio in pipeline(text, voice=voice):
        arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
        chunks.append(arr.astype(np.float32).flatten())

    if chunks:
        samples = np.concatenate(chunks)
    else:
        samples = np.zeros(int(SAMPLE_RATE * 1.0), dtype=np.float32)  # 1s silence

    _write_wav(wav_path, samples, SAMPLE_RATE)

    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-codec:a", "libmp3lame", "-b:a", "192k", mp3_path],
        check=True, capture_output=True,
    )
    try:
        os.remove(wav_path)
    except OSError:
        pass
    return mp3_path


def _write_wav(path: str, samples: np.ndarray, rate: int) -> None:
    """Write a float32 [-1, 1] mono signal to a 16-bit PCM WAV file."""
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
