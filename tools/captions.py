"""Caption generator using local Whisper (model=base).

The Whisper model is loaded exactly once at module scope (lazily, with an
explicit `warmup()` hook for startup). Each narration MP3 is transcribed with
word-level timestamps and written to a sibling .srt file, segmented to a
maximum of 8 words per caption for readability.
"""

import os
import asyncio
import logging

log = logging.getLogger("videoforge.captions")

MAX_WORDS_PER_SEGMENT = 8

_model = None


def warmup() -> None:
    """Eagerly load the Whisper model. Call once at startup."""
    _get_model()
    log.info("Whisper model ready.")


def _get_model():
    global _model
    if _model is None:
        import whisper
        log.info("Loading Whisper model 'base'...")
        _model = whisper.load_model("base")
    return _model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def transcribe_all(audio: dict) -> dict:
    """Transcribe every narration MP3. `audio` is {scene_index: mp3_path}.
    Returns {scene_index: srt_path}. Whisper is blocking, so each call is
    offloaded to a worker thread; runs sequentially to avoid model contention."""
    out: dict[int, str] = {}
    for idx, mp3_path in audio.items():
        out[idx] = await asyncio.to_thread(transcribe, mp3_path)
    return out


def transcribe(mp3_path: str) -> str:
    """Transcribe one MP3 to an SRT file placed next to it. Returns srt_path."""
    model = _get_model()
    result = model.transcribe(mp3_path, word_timestamps=True)

    segments = _build_segments(result)
    srt_path = os.path.splitext(mp3_path)[0] + ".srt"
    _write_srt(segments, srt_path)
    return srt_path


def _build_segments(result: dict) -> list[tuple[float, float, str]]:
    """Flatten Whisper word timestamps into <=8-word caption segments."""
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []) or []:
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append((float(w.get("start", 0.0)), float(w.get("end", 0.0)), text))

    # Fallback: no word timestamps -> use whole-segment timings.
    if not words:
        segs = []
        for seg in result.get("segments", []):
            txt = (seg.get("text") or "").strip()
            if txt:
                segs.append((float(seg.get("start", 0.0)),
                             float(seg.get("end", 0.0)), txt))
        return segs

    out: list[tuple[float, float, str]] = []
    bucket: list[tuple[float, float, str]] = []
    for word in words:
        bucket.append(word)
        if len(bucket) >= MAX_WORDS_PER_SEGMENT:
            out.append((bucket[0][0], bucket[-1][1],
                        " ".join(w[2] for w in bucket)))
            bucket = []
    if bucket:
        out.append((bucket[0][0], bucket[-1][1],
                    " ".join(w[2] for w in bucket)))
    return out


def _write_srt(segments: list[tuple[float, float, str]], path: str) -> None:
    lines = []
    for i, (start, end, text) in enumerate(segments, start=1):
        if end <= start:
            end = start + 0.5
        lines.append(str(i))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(text.strip())
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _ts(seconds: float) -> str:
    """Format seconds as SRT timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
