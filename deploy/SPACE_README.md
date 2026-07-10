---
title: MinecraftCast
emoji: 🎮
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: AI Minecraft faceless video generator (REST API)
---

# MinecraftCast — REST API

Type a topic, get a finished Minecraft-style narrated video: two animated
cartoon avatars have an AI-voiced conversation over gameplay footage, with
subtitles and background music.

This Space runs the FastAPI service. Open **`/docs`** for the interactive API.

## Endpoints
- `POST /generate` — queue a video (`{"topic": "...", "duration_minutes": 1.5}`) → `{job_id}`
- `GET /job/{job_id}` — poll status (`queued` → `processing` → `complete`)
- `GET /download/{job_id}` — download the finished MP4
- `GET /health` — liveness

## Notes
- Voice uses free Microsoft **edge-tts** (no key). Script generation uses **Groq**
  — set `GROQ_API_KEY` as a Space secret.
- Storage is ephemeral: download your video soon after it completes.
