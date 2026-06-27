# VideoForgeAgent

VideoForgeAgent is a fully autonomous, broadcast-quality AI video generation agent for the
[CROO Agent Store](https://agent.croo.network/). It runs as a single long-lived Python process
that connects to CROO over WebSocket, auto-accepts negotiations, and — the moment an order is
paid — runs an end-to-end pipeline: it writes a retention-optimized script with your chosen AI
provider (Groq, OpenAI, Gemini, Mistral, Ollama, or Anthropic),
sources matching stock footage from Pexels and Pixabay, narrates it with local Kokoro TTS,
generates word-timed captions with local Whisper, scores it with mood-matched background music,
assembles everything with FFmpeg, uploads the finished MP4 to Cloudflare R2, and delivers the
public video URL back to the requester. There is **no HTTP server** — the CROO WebSocket is the
only interface.

---

## 1. What it does

| Stage | Tool | Output |
| ----- | ---- | ------ |
| Script | Configurable AI provider (Groq / OpenAI / Gemini / Mistral / Ollama / Anthropic) | Strict-JSON, retention-optimized script |
| Visuals | Pexels + Pixabay (async) | One clip/image per scene (+ FFmpeg fallback card) |
| Voice | Kokoro TTS (local) | Per-scene narration MP3 |
| Captions | Whisper `base` (local) | Per-scene SRT (≤8 words/segment) |
| Music | Pixabay Music | Mood-matched bed (cached by mood) |
| Assembly | FFmpeg (subprocess) | Color-graded, captioned, mixed MP4 |
| Storage | Cloudflare R2 | Public video URL |

Supported styles: `explainer`, `documentary`, `shorts` (vertical 1080×1920), `faceless`, `slideshow`.

### Choosing an AI Provider

The script engine is provider-agnostic. Set `AI_PROVIDER` (and that provider's key) in `.env`.
The active provider is logged on startup (e.g. `AI provider: Groq (llama-3.3-70b-versatile)`).

| Provider  | Cost      | Speed   | Quality  | Setup                    |
|-----------|-----------|---------|----------|--------------------------|
| Groq      | Free tier | Fastest | Great    | console.groq.com         |
| Ollama    | Free      | Depends | Good     | ollama.ai (local)        |
| Gemini    | Free tier | Fast    | Great    | aistudio.google.com      |
| Mistral   | Free tier | Fast    | Good     | console.mistral.ai       |
| OpenAI    | Paid      | Fast    | Best     | platform.openai.com      |
| Anthropic | Paid      | Fast    | Best     | console.anthropic.com    |

Groq is the default. To switch, e.g. to Gemini: set `AI_PROVIDER=gemini` and `GEMINI_API_KEY=...`.
Ollama runs fully local (free) — start Ollama, `ollama pull llama3.2`, set `AI_PROVIDER=ollama`.

---

## 2. Listing on the CROO Agent Store (Dashboard steps)

These are manual steps performed once in the [CROO Dashboard](https://agent.croo.network/):

1. **Create an Agent** → set name, description, and 1–5 skill tags (e.g. `video`, `content`, `media`).
2. **Add a Service** via the "+ Add Service" wizard:
   - **Service Name:** `AI Video Generation`
   - **Price:** e.g. `5.00` USDC per call
   - **Description:** "Generates a complete narrated, captioned, music-scored video from a topic."
   - **SLA:** e.g. `0h 30m` (delivery deadline)
   - **Deliverable:** **Schema** — field `video_url` (type `string`, format `url`)
   - **Requirements:** **Schema** — see §7 below
3. **Save** the service. When all required fields are complete, the Dashboard issues an
   **API Key** (`croo_sk_...`) and shows the SDK connection steps.
4. Put that key in `CROO_SDK_KEY` and start this process. The Agent transitions from
   `draft` → `online` automatically once the WebSocket handshake completes.

> ⚠️ One API Key allows **one active WebSocket connection** at a time. Don't run two copies
> of this process with the same key.

---

## 3. Environment variables

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `CROO_API_URL` | yes | CROO API base URL (`https://api.croo.network`) |
| `CROO_WS_URL` | yes | CROO WebSocket URL (`wss://api.croo.network/ws`) |
| `CROO_SDK_KEY` | yes | Agent API key from the Dashboard (`croo_sk_...`) |
| `AI_PROVIDER` | no | AI backend: `groq` (default) \| `openai` \| `ollama` \| `gemini` \| `mistral` \| `anthropic` |
| `GROQ_API_KEY` | if `groq` | Groq API key (default provider) |
| `OPENAI_API_KEY` | if `openai` | OpenAI API key |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | if `ollama` | Local Ollama endpoint + model (default `llama3.2`) |
| `GEMINI_API_KEY` | if `gemini` | Google Gemini API key |
| `MISTRAL_API_KEY` | if `mistral` | Mistral API key |
| `ANTHROPIC_API_KEY` | if `anthropic` | Anthropic API key |
| `PEXELS_API_KEY` | yes | Pexels API key (video + image) |
| `PIXABAY_API_KEY` | yes | Pixabay API key (video, image, music) |
| `CLOUDFLARE_ACCOUNT_ID` | yes | Cloudflare account id (R2 endpoint) |
| `CLOUDFLARE_R2_ACCESS_KEY` | yes | R2 S3 access key id |
| `CLOUDFLARE_R2_SECRET_KEY` | yes | R2 S3 secret access key |
| `CLOUDFLARE_R2_BUCKET` | yes | R2 bucket name (default `videoforge-outputs`) |
| `CLOUDFLARE_R2_PUBLIC_DOMAIN` | no | Public dev domain from the bucket settings (e.g. `pub-xxxx.r2.dev`); falls back to `pub-<account[:8]>.r2.dev` |
| `LOG_LEVEL` | no | Logging level (default `INFO`) |

Copy `.env.example` to `.env` and fill in the values.

---

## 4. Run locally

Prerequisites: **Python 3.11**, **FFmpeg**, **espeak-ng**, **libsndfile** installed on the host.

```bash
# 1. Install system deps (Debian/Ubuntu example)
sudo apt-get install -y ffmpeg libsndfile1 espeak-ng fonts-dejavu-core

# 2. Install Python deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env   # then edit .env

# 4. Run (loads Kokoro + Whisper once, then listens forever)
python main.py
```

The first run downloads the Whisper `base` model and Kokoro voices. The process stays alive,
prints `VideoForgeAgent online`, and handles negotiations/orders as they arrive.

### Run with Docker

```bash
docker build -t videoforge .
docker run --env-file .env videoforge
```

---

## 5. Deploy on Railway

1. Push this directory to a GitHub repo.
2. In [Railway](https://railway.app/): **New Project → Deploy from GitHub repo**.
3. Railway auto-detects the **Dockerfile** and builds it (FFmpeg + espeak-ng included).
4. Add all variables from §3 under **Variables** (paste your `.env` contents).
5. This is a **worker**, not a web service — no public port is needed. In **Settings →
   Networking**, leave HTTP networking disabled. Railway keeps the process running and
   restarts it on crash.
6. Recommended: bump the service to an instance with ≥2 GB RAM (Whisper + Kokoro + FFmpeg)
   and persistent CPU. Watch the deploy logs for `VideoForgeAgent online`.

> Because models load once at startup, keep the service **always-on** (disable sleep) so you
> don't pay the model-load cost on every order.

---

## 6. CROO SDK methods used

| SDK surface | Where |
| ----------- | ----- |
| `client.connect_websocket()` | `main.py` — opens the event stream |
| `EventType.NEGOTIATION_CREATED` | `main.py` — auto-accept trigger |
| `EventType.ORDER_PAID` | `main.py` — pipeline trigger |
| `EventType.ORDER_COMPLETED` | `main.py` — settlement log |
| `client.accept_negotiation(negotiation_id)` | `main.py` — auto-accept |
| `client.get_order(order_id)` | `main.py` — read requirements |
| `client.deliver_order(order_id, DeliverRequest(...))` | `main.py` — deliver `video_url` |
| `client.reject_order(order_id, reason)` | `main.py` — failure path |
| `DeliverableType.SCHEMA` | `main.py` — structured JSON deliverable |
| `APIError`, `is_insufficient_balance`, `is_not_found` | `main.py` — error handling |
| `client.close()` | `main.py` — shutdown |

> The Python SDK delivers events to **synchronous** callbacks; every async call is wrapped in
> `asyncio.create_task()`. The deliverable request class is `DeliverOrderRequest` in the SDK
> (imported as `DeliverRequest` here for clarity).

---

## 7. Service config used in the Dashboard

**Price:** `5.00` USDC/call · **SLA:** `0h 30m` · **Deliverable:** Schema · **Requirements:** Schema

**Requirements schema (what the requester submits):**

| Field | Type | Required | Description |
| ----- | ---- | -------- | ----------- |
| `topic` | string | yes | What the video is about |
| `style` | string | no | `explainer` \| `documentary` \| `shorts` \| `faceless` \| `slideshow` (default `faceless`) |
| `duration_minutes` | number | no | Target length in minutes (ignored for `shorts`; default `3`) |
| `tone` | string | no | e.g. `inspiring`, `serious`, `playful` |
| `target_audience` | string | no | e.g. `beginners`, `investors` |

**Deliverable schema (what the agent returns):**

| Field | Type | Format | Description |
| ----- | ---- | ------ | ----------- |
| `video_url` | string | url | Public URL of the finished MP4 |

---

## 8. Example order input JSON

```json
{
  "topic": "Why the ocean is salty",
  "style": "faceless",
  "duration_minutes": 4,
  "tone": "curious and authoritative",
  "target_audience": "science-curious adults"
}
```

A `shorts` example (auto-forced to 1080×1920, ≤58s):

```json
{
  "topic": "The 2-minute rule for beating procrastination",
  "style": "shorts",
  "target_audience": "students"
}
```

The agent delivers:

```json
{ "video_url": "https://pub-abcd1234ef567890.r2.dev/videos/<order_id>/output.mp4" }
```

---

## Project layout

```
videoforge/
├── main.py            # CROO WebSocket listener + event handlers
├── pipeline.py        # Orchestrates all tools in sequence
├── tools/
│   ├── script.py      # AI script engine (the core skill)
│   ├── providers.py   # Pluggable AI provider factory (groq/openai/ollama/gemini/mistral/anthropic)
│   ├── visuals.py     # Pexels + Pixabay async downloader
│   ├── voice.py       # Kokoro TTS (local, loaded once)
│   ├── captions.py    # Whisper base (local, loaded once)
│   ├── assembly.py    # FFmpeg pipeline (subprocess)
│   └── music.py       # Pixabay music (cached by mood)
├── storage/
│   └── r2.py          # Cloudflare R2 upload
├── db.py              # SQLite job tracking (aiosqlite)
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md
```
