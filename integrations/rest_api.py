"""FastAPI REST wrapper for MinecraftCast (optional layer).

Exposes the core pipeline over HTTP so any platform that can make requests —
Discord bots, web apps, other agents — can generate videos. Generation runs in a
background task; poll ``/job/{job_id}`` for status.

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

    config = VideoConfig(
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
    background_tasks.add_task(_run_job, config)
    return {"job_id": job_id, "status": "queued"}


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
