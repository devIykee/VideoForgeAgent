"""Minecraft footage fetcher for MinecraftCast.

Three sources, chosen by :attr:`VideoConfig.footage_source`:

* ``youtube``  — download no-commentary gameplay with yt-dlp (falls back to
                 Archive.org on failure).
* ``archive``  — pull a Creative-Commons / public-domain clip from Archive.org.
* ``upload``   — use a file the user dropped in ``uploads/``.

Every path returns a single local video file the pipeline can slice from.
"""

import os
from typing import Optional

from config import VideoConfig


def _job_dir(job_id: str) -> str:
    """Return (creating if needed) the temp working dir for a job."""
    path = f"/tmp/minecraftcast/{job_id}"
    os.makedirs(path, exist_ok=True)
    return path


async def fetch_footage(config: VideoConfig, job_id: str) -> str:
    """Fetch Minecraft footage for this job. Returns a local file path."""
    if config.footage_source == "upload":
        return _get_uploaded_footage()

    search_query = f"Minecraft {config.footage_type} gameplay no commentary"

    if config.footage_source == "youtube":
        path = await _fetch_youtube(search_query, job_id)
        if path:
            return path
        print("      YouTube fetch failed, trying Archive.org...")

    return await _fetch_archive(search_query, job_id)


async def _fetch_youtube(query: str, job_id: str) -> Optional[str]:
    """Download Minecraft footage via yt-dlp. Returns path or None on failure."""
    import yt_dlp

    output_path = f"{_job_dir(job_id)}/footage.mp4"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": output_path.replace(".mp4", ".%(ext)s"),
        "default_search": "ytsearch10",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "match_filter": yt_dlp.utils.match_filter_func("duration > 300 & duration < 3600"),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"ytsearch5:{query}"])
        for ext in ("mp4", "webm", "mkv"):
            candidate = output_path.replace(".mp4", f".{ext}")
            if os.path.exists(candidate):
                return candidate
    except Exception as e:  # noqa: BLE001 — any download error should fall back
        print(f"      yt-dlp error: {e}")
    return None


async def _fetch_archive(query: str, job_id: str) -> str:
    """Download Minecraft footage from Archive.org. Raises if nothing usable."""
    import aiohttp

    search_url = "https://archive.org/advancedsearch.php"
    params = {
        "q": f"subject:(minecraft) {query}",
        "fl[]": ["identifier", "title"],
        "mediatype": "movies",
        "rows": 10,
        "output": "json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(search_url, params=params) as resp:
            data = await resp.json(content_type=None)

        docs = data.get("response", {}).get("docs", [])
        if not docs:
            raise RuntimeError("No footage found on Archive.org")

        # Try each result until one yields a downloadable MP4.
        last_error: Exception | None = None
        for doc in docs:
            identifier = doc["identifier"]
            try:
                meta_url = f"https://archive.org/metadata/{identifier}"
                async with session.get(meta_url) as resp:
                    meta = await resp.json(content_type=None)

                files = meta.get("files", [])
                mp4_files = [f for f in files if f.get("name", "").lower().endswith(".mp4")]
                if not mp4_files:
                    continue

                file_name = mp4_files[0]["name"]
                download_url = f"https://archive.org/download/{identifier}/{file_name}"

                output_path = f"{_job_dir(job_id)}/footage.mp4"
                async with session.get(download_url) as resp:
                    if resp.status != 200:
                        continue
                    with open(output_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)

                if os.path.getsize(output_path) > 1024:
                    return output_path
            except Exception as e:  # noqa: BLE001 — try the next candidate
                last_error = e
                continue

        raise RuntimeError(f"No usable MP4 found on Archive.org (last error: {last_error})")


def _get_uploaded_footage() -> str:
    """Find user-uploaded footage in the uploads/ folder. Raises if none."""
    video_extensions = (".mp4", ".mkv", ".webm", ".avi", ".mov")
    os.makedirs("uploads", exist_ok=True)
    for file in os.listdir("uploads"):
        if file.lower().endswith(video_extensions):
            return os.path.join("uploads", file)
    raise FileNotFoundError(
        "No video file found in uploads/ folder.\n"
        "Drop your Minecraft footage there and try again."
    )
