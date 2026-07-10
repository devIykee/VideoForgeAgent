"""FastAPI REST wrapper for MinecraftCast (optional layer).

Exposes the core pipeline over HTTP so any platform that can make requests —
Discord bots, web apps, other agents — can generate videos.

Two entry points:
* ``POST /generate`` — async: queues the job, poll ``/job/{job_id}``, then
  ``/download/{job_id}``. Use where the host keeps CPU alive between requests.
* ``POST /render``   — sync: renders inside the request and returns the MP4.
  Use on request-scoped-CPU platforms (e.g. Google Cloud Run).

Run: ``uvicorn integrations.rest_api:app --host 0.0.0.0 --port 8000``
"""

import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

import db
from pipeline import run as run_pipeline
from config import VideoConfig, CharacterConfig

app = FastAPI(
    title="MinecraftCast API",
    description="AI Minecraft faceless video generator",
    version="1.0.0",
)


class GenerateRequest(BaseModel):
    """Request body for POST /generate."""

    topic: str
    char1_name: str = "Alex"
    char1_personality: str = "energetic and funny"
    char2_name: str = "Steve"
    char2_personality: str = "calm and skeptical"
    voice_provider: str = "edge"
    duration_minutes: float = 3.0
    footage_source: str = "youtube"
    footage_type: str = "survival gameplay"


def _config_from_request(req: "GenerateRequest", job_id: str) -> VideoConfig:
    """Build a VideoConfig from an API request body."""
    return VideoConfig(
        topic=req.topic,
        char1=CharacterConfig(
            name=req.char1_name,
            personality=req.char1_personality,
            voice_provider=req.voice_provider,
            avatar_skin="alex", shirt_color="#6AA84F",
        ),
        char2=CharacterConfig(
            name=req.char2_name,
            personality=req.char2_personality,
            voice_provider=req.voice_provider,
            avatar_skin="steve", shirt_color="#3B6BB5",
        ),
        duration_minutes=req.duration_minutes,
        footage_source=req.footage_source,
        footage_type=req.footage_type,
        job_id=job_id,
    )


async def _run_job(config: VideoConfig) -> None:
    """Background worker: run the pipeline and record status in the DB."""
    try:
        await db.update_job(config.job_id, status="processing")
        final_path = await run_pipeline(config)
        await db.update_job(config.job_id, status="complete",
                            progress=100, output_url=final_path)
    except Exception as e:  # noqa: BLE001
        await db.update_job(config.job_id, status="failed", error=str(e))


@app.on_event("startup")
async def _startup() -> None:
    """Ensure the jobs table exists before serving requests."""
    await db.init_db()


@app.post("/generate")
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Queue a video generation job. Returns a job_id to poll."""
    job_id = str(uuid.uuid4())
    await db.create_job(job_id, req.dict())

    config = _config_from_request(req, job_id)
    background_tasks.add_task(_run_job, config)
    return {"job_id": job_id, "status": "queued"}


@app.post("/render")
async def render(req: GenerateRequest):
    """Synchronously render a video and return the MP4 in the response.

    Unlike ``/generate`` (which renders in a background task and is polled), this
    runs the whole pipeline *inside the request*. Use it on platforms that only
    allocate CPU during an active request — e.g. Google Cloud Run — where a
    background task would be frozen after the response is sent. The client waits
    the full render time (~5–8 min for a short clip), so keep durations small.
    """
    job_id = str(uuid.uuid4())
    config = _config_from_request(req, job_id)
    try:
        final_path = await run_pipeline(config)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Render failed: {e}")
    return FileResponse(final_path, media_type="video/mp4",
                        filename=f"{job_id}.mp4")


@app.get("/job/{job_id}")
async def job_status(job_id: str):
    """Return the current status of a generation job."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/download/{job_id}")
async def download(job_id: str):
    """Download the finished MP4 for a completed job."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") != "complete":
        raise HTTPException(409, f"Job not ready (status: {job.get('status')})")
    path = job.get("output_url")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Output file missing")
    return FileResponse(path, media_type="video/mp4",
                        filename=f"{job_id}.mp4")


@app.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok", "service": "MinecraftCast"}
