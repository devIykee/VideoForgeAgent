"""Stock-footage fetcher: Pexels + Pixabay, async, with FFmpeg fallback cards.

For each scene we try, in priority order:
  1. Pexels video  2. Pexels image  3. Pixabay video  4. Pixabay image
  5. AI query optimization (retry the above with better terms)
  6. FFmpeg-generated solid color card with the scene title

All scenes are fetched in parallel under a semaphore(5).
"""

import os
import asyncio
import logging
import subprocess

import aiohttp

from tools import script as script_engine

log = logging.getLogger("videoforge.visuals")

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
PEXELS_IMAGE_URL = "https://api.pexels.com/v1/search"
PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
PIXABAY_IMAGE_URL = "https://pixabay.com/api/"

_CONCURRENCY = 5
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=120)


def _media_dir(job_id: str) -> str:
    d = f"/tmp/videoforge/{job_id}/media"
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_all(script: dict, job_id: str) -> dict:
    """Fetch a visual for every scene in parallel. Returns
    {scene_index: local_file_path}."""
    resolution = script.get("target_resolution", "1920x1080")
    scenes = [s for ch in script.get("chapters", []) for s in ch.get("scenes", [])]

    sem = asyncio.Semaphore(_CONCURRENCY)
    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:

        async def _guarded(scene):
            async with sem:
                return await fetch_scene_visual(session, scene, job_id, resolution)

        results = await asyncio.gather(*[_guarded(s) for s in scenes])

    return {scene["scene_index"]: path for scene, path in zip(scenes, results)}


# Spec alias.
fetch_all_visuals = fetch_all


async def fetch_scene_visual(session, scene, job_id, resolution="1920x1080") -> str:
    """Resolve a single scene to a local media file path, trying every source
    and finally generating a fallback card."""
    idx = scene["scene_index"]
    media_dir = _media_dir(job_id)

    attempts = [
        ("video", PEXELS_VIDEO_URL, scene.get("pexels_video_query"), "pexels"),
        ("image", PEXELS_IMAGE_URL, scene.get("pexels_image_query"), "pexels"),
        ("video", PIXABAY_VIDEO_URL, scene.get("pixabay_video_query"), "pixabay"),
        ("image", PIXABAY_IMAGE_URL, scene.get("pixabay_image_query"), "pixabay"),
    ]

    for kind, url, query, provider in attempts:
        if not query:
            continue
        path = await _try_source(session, provider, kind, url, query, media_dir, idx)
        if path:
            log.info("Scene %s: %s %s -> '%s'", idx, provider, kind, query)
            return path

    # Last resort before fallback card: ask the AI provider for better queries.
    try:
        opt = await script_engine.optimize_search_queries(
            scene.get("visual_description", "") or scene.get("narration", "")
        )
        for q in opt.get("queries", [])[:3]:
            for provider, kind, url in (
                ("pexels", "video", PEXELS_VIDEO_URL),
                ("pixabay", "video", PIXABAY_VIDEO_URL),
                ("pexels", "image", PEXELS_IMAGE_URL),
            ):
                path = await _try_source(session, provider, kind, url, q, media_dir, idx)
                if path:
                    log.info("Scene %s: recovered via optimized query '%s'", idx, q)
                    return path
    except Exception as e:  # query optimization is best-effort
        log.warning("Scene %s query optimization failed: %s", idx, e)

    log.warning("Scene %s: no stock media found, generating fallback card", idx)
    return _generate_fallback_card(scene, media_dir, idx, resolution)


# ---------------------------------------------------------------------------
# Source handlers
# ---------------------------------------------------------------------------

async def _try_source(session, provider, kind, url, query, media_dir, idx) -> str | None:
    try:
        if provider == "pexels":
            download_url, ext = await (
                _pexels_video(session, url, query) if kind == "video"
                else _pexels_image(session, url, query)
            )
        else:
            download_url, ext = await (
                _pixabay_video(session, url, query) if kind == "video"
                else _pixabay_image(session, url, query)
            )
        if not download_url:
            return None
        dest = os.path.join(media_dir, f"scene_{idx}.{ext}")
        ok = await _download(session, download_url, dest)
        return dest if ok else None
    except Exception as e:
        log.debug("Scene %s %s/%s '%s' failed: %s", idx, provider, kind, query, e)
        return None


async def _pexels_video(session, url, query):
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": 5}
    async with session.get(url, headers=headers, params=params) as r:
        if r.status != 200:
            return None, None
        data = await r.json()
    videos = data.get("videos", [])
    if not videos:
        return None, None
    # Pick the best HD-ish file (highest height <= 1080, prefer .mp4).
    best_url, best_h = None, -1
    for v in videos:
        for f in v.get("video_files", []):
            h = f.get("height") or 0
            link = f.get("link")
            if not link:
                continue
            score = h if h <= 1080 else 1080 - (h - 1080)
            if score > best_h:
                best_h, best_url = score, link
    return best_url, "mp4"


async def _pexels_image(session, url, query):
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": 5}
    async with session.get(url, headers=headers, params=params) as r:
        if r.status != 200:
            return None, None
        data = await r.json()
    photos = data.get("photos", [])
    if not photos:
        return None, None
    src = photos[0].get("src", {})
    link = src.get("large2x") or src.get("large") or src.get("original")
    return link, "jpg"


async def _pixabay_video(session, url, query):
    if not PIXABAY_API_KEY:
        return None, None
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 5}
    async with session.get(url, params=params) as r:
        if r.status != 200:
            return None, None
        data = await r.json()
    hits = data.get("hits", [])
    if not hits:
        return None, None
    files = hits[0].get("videos", {})
    chosen = files.get("large") or files.get("medium") or files.get("small")
    return (chosen.get("url") if chosen else None), "mp4"


async def _pixabay_image(session, url, query):
    if not PIXABAY_API_KEY:
        return None, None
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 5, "image_type": "photo"}
    async with session.get(url, params=params) as r:
        if r.status != 200:
            return None, None
        data = await r.json()
    hits = data.get("hits", [])
    if not hits:
        return None, None
    link = hits[0].get("largeImageURL") or hits[0].get("webformatURL")
    return link, "jpg"


async def _download(session, url, dest) -> bool:
    async with session.get(url) as r:
        if r.status != 200:
            return False
        with open(dest, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 16):
                f.write(chunk)
    return os.path.getsize(dest) > 1024


# ---------------------------------------------------------------------------
# Fallback card
# ---------------------------------------------------------------------------

def _generate_fallback_card(scene, media_dir, idx, resolution) -> str:
    """Render a solid-color title card image via FFmpeg as the ultimate
    fallback so the pipeline never stalls on a missing clip."""
    dest = os.path.join(media_dir, f"scene_{idx}.png")
    title = (scene.get("text_overlay") or scene.get("visual_description")
             or scene.get("narration", "") or "VideoForge")
    title = title[:60].replace(":", " ").replace("'", " ").replace("\\", " ")

    fontfile = _find_font()
    drawtext = (
        f"drawtext=text='{_escape(title)}':fontcolor=white:fontsize=48:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=10"
    )
    if fontfile:
        drawtext = f"drawtext=fontfile='{fontfile}':" + drawtext[len("drawtext="):]

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x1a1a2e:s={resolution.replace('x', 'x')}:d=1",
        "-vf", drawtext,
        "-frames:v", "1",
        dest,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # If drawtext fails (no font), produce a plain color frame.
        log.warning("Fallback drawtext failed (%s); using plain card",
                    e.stderr.decode("utf-8", "ignore")[-200:])
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x1a1a2e:s={resolution}:d=1",
            "-frames:v", "1", dest,
        ], check=True, capture_output=True)
    return dest


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "")


def _find_font() -> str | None:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if os.path.exists(p):
            return p
    return None
