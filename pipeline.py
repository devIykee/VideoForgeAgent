"""End-to-end video generation pipeline.

Orchestrates every tool in sequence and reports progress to the jobs table.
Returns the public R2 URL of the finished video. Temp files for the job live
under /tmp/videoforge/{job_id}/ and are removed after upload.
"""

import os
import shutil
import logging

import db
from tools import script as script_tool
from tools import visuals
from tools import voice
from tools import captions
from tools import music
from tools import assembly
from storage import r2

log = logging.getLogger("videoforge.pipeline")


async def run(job_id: str, req: dict) -> str:
    """Full pipeline. Returns the R2 video URL."""
    log.info("[%s] Pipeline start: %s", job_id, req)
    await db.update_job(job_id, status="running", progress=0)

    try:
        script = await script_tool.generate(req)
        await db.update_job(job_id, progress=10)

        media = await visuals.fetch_all(script, job_id)
        await db.update_job(job_id, progress=30)

        audio = await voice.generate_all(script, job_id)
        await db.update_job(job_id, progress=50)

        srt = await captions.transcribe_all(audio)
        await db.update_job(job_id, progress=60)

        music_path = await music.fetch(script["background_music_mood"])
        await db.update_job(job_id, progress=65)

        video = await assembly.build(job_id, media, audio, srt, music_path, script)
        await db.update_job(job_id, progress=90)

        url = await r2.upload(job_id, video)
        await db.update_job(job_id, status="completed", progress=100, output_url=url)

        cleanup(job_id)
        log.info("[%s] Pipeline complete: %s", job_id, url)
        return url

    except Exception as e:
        log.exception("[%s] Pipeline failed", job_id)
        await db.update_job(job_id, status="failed", error=str(e))
        cleanup(job_id)
        raise


def cleanup(job_id: str) -> None:
    """Remove the job's working directory (keeps the shared music cache)."""
    path = f"/tmp/videoforge/{job_id}"
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
        log.info("[%s] Cleaned up %s", job_id, path)
