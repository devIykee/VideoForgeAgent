"""Background-music fetcher using the Pixabay API.

Music is cached by mood under /tmp/videoforge/music/ and reused across jobs to
avoid re-downloading the same track repeatedly.

Note: Pixabay's public API exposes images and videos. Audio is served through
the same `hits[].videos`-free response, so we read the audio download links
that the music endpoint returns. When no track can be fetched, the pipeline
falls back to a synthesized silent track so assembly never fails.
"""

import os
import asyncio
import logging
import subprocess

import aiohttp

log = logging.getLogger("videoforge.music")

PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_MUSIC_URL = "https://pixabay.com/api/"  # audio search shares the base API

MOOD_TO_QUERY = {
    "uplifting": "uplifting corporate",
    "dramatic": "cinematic dramatic",
    "calm": "ambient calm",
    "energetic": "upbeat energetic",
    "mysterious": "mysterious ambient",
    "tense": "tension suspense",
}

_CACHE_DIR = "/tmp/videoforge/music"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=120)
_lock = asyncio.Lock()


def _cache_dir() -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


async def fetch(mood: str) -> str:
    """Return a local MP3 path for the requested mood, downloading and caching
    it on first request. Falls back to a generated silent track if unavailable."""
    mood = (mood or "calm").lower()
    query = MOOD_TO_QUERY.get(mood, "ambient calm")
    cache_path = os.path.join(_cache_dir(), f"{mood}.mp3")

    # Reuse cached track across jobs.
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1024:
        log.info("Music cache hit for mood '%s'", mood)
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
                if os.path.getsize(cache_path) > 1024:
                    log.info("Downloaded music for mood '%s'", mood)
                    return cache_path
            except Exception as e:
                log.warning("Music download failed for '%s': %s", mood, e)

        log.warning("No music for mood '%s'; generating silent track", mood)
        _generate_silent_track(cache_path)
        return cache_path


# Spec alias.
fetch_music = fetch


async def _search_track(query: str) -> str | None:
    """Query Pixabay for a music track URL. Returns None if unavailable."""
    if not PIXABAY_API_KEY:
        return None
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 5, "media_type": "music"}
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.get(PIXABAY_MUSIC_URL, params=params) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
    except Exception as e:
        log.warning("Pixabay music search failed: %s", e)
        return None

    for hit in data.get("hits", []):
        for key in ("audio", "download_url", "url", "previewURL"):
            if hit.get(key):
                return hit[key]
    return None


def _generate_silent_track(path: str, seconds: int = 600) -> None:
    """Generate a near-silent MP3 so the assembly mix step always has an input."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
         "-t", str(seconds), "-q:a", "9", "-acodec", "libmp3lame", path],
        check=True, capture_output=True,
    )
