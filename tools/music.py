"""Background-music fetcher using the Pixabay API.

Music is cached per mood under ``/tmp/minecraftcast/music/`` and reused across
jobs to avoid re-downloading the same track. When no track can be fetched (no
API key, network failure, empty results), a near-silent track is synthesized so
the assembly mix step always has an input and never fails.
"""

import os
import asyncio
import subprocess

import aiohttp

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_URL = "https://pixabay.com/api/"  # audio search shares the base API

# Script moods -> Pixabay search queries.
MOOD_TO_QUERY = {
    "chill": "chill lofi ambient",
    "tense": "tension suspense dark",
    "upbeat": "upbeat energetic gaming",
    "mysterious": "mysterious ambient cinematic",
}

_CACHE_DIR = "/tmp/minecraftcast/music"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=120)
_lock = asyncio.Lock()


def _cache_dir() -> str:
    """Return (creating if needed) the shared music cache directory."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


async def fetch(mood: str, job_id: str) -> str:
    """Return a local MP3 path for ``mood``, downloading + caching on first use.

    ``job_id`` is accepted for interface symmetry with the rest of the pipeline;
    tracks are cached globally by mood, not per job. Always returns a usable
    path — a synthesized silent track is used as a last resort.
    """
    mood = (mood or "chill").lower()
    query = MOOD_TO_QUERY.get(mood, "chill lofi ambient")
    cache_path = os.path.join(_cache_dir(), f"{mood}.mp3")

    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1024:
        return cache_path

    async with _lock:  # avoid two jobs racing to write the same cache file
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1024:
            return cache_path

        url = await _search_track(query)
        if url:
            try:
                async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                    async with session.get(url) as r:
                        if r.status == 200:
                            with open(cache_path, "wb") as f:
                                async for chunk in r.content.iter_chunked(1 << 16):
                                    f.write(chunk)
                if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1024:
                    return cache_path
            except Exception as e:  # noqa: BLE001 — fall back to silence
                print(f"      Music download failed for '{mood}': {e}")

        _generate_silent_track(cache_path)
        return cache_path


async def _search_track(query: str) -> str | None:
    """Query Pixabay for a music track URL. Returns None if unavailable."""
    if not PIXABAY_API_KEY:
        return None
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 5, "media_type": "music"}
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.get(PIXABAY_URL, params=params) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
    except Exception as e:  # noqa: BLE001
        print(f"      Pixabay music search failed: {e}")
        return None

    for hit in data.get("hits", []):
        for key in ("audio", "download_url", "url", "previewURL"):
            if hit.get(key):
                return hit[key]
    return None


def _generate_silent_track(path: str, seconds: int = 900) -> None:
    """Generate a near-silent MP3 so the assembly mix step always has an input."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
         "-t", str(seconds), "-q:a", "9", "-acodec", "libmp3lame", path],
        check=True, capture_output=True,
    )
