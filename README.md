<div align="center">

# MinecraftCast 🎮

**Faceless Minecraft content, zero effort.**

Type your idea → get a finished, upload-ready MP4.

Two animated South Park–style cartoon characters have an AI-voiced conversation
over Minecraft gameplay footage, complete with lip-synced mouths, speaker
subtitles, and low-volume background music.

</div>

---

## Table of contents

- [What it is](#what-it-is)
- [Core philosophy](#core-philosophy)
- [Feature overview](#feature-overview)
- [The finished video](#the-finished-video)
- [Architecture at a glance](#architecture-at-a-glance)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration (`.env` reference)](#configuration-env-reference)
- [Usage — the interactive CLI](#usage--the-interactive-cli)
- [The 6-step onboarding flow](#the-6-step-onboarding-flow)
- [Voice system](#voice-system)
- [Avatar system](#avatar-system)
- [Footage system](#footage-system)
- [Music system](#music-system)
- [The pipeline, stage by stage](#the-pipeline-stage-by-stage)
- [Data models](#data-models)
- [The script schema](#the-script-schema)
- [Project structure](#project-structure)
- [Integration layers](#integration-layers)
  - [REST API](#rest-api)
  - [MCP server](#mcp-server)
  - [CROO Agent Store provider](#croo-agent-store-provider)
- [CROO dashboard service config](#croo-dashboard-service-config)
- [Docker](#docker)
- [Cost & performance](#cost--performance)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Extending MinecraftCast](#extending-minecraftcast)
- [Legal & content notes](#legal--content-notes)

---

## What it is

MinecraftCast is a command-line tool for **faceless Minecraft YouTube creators**.
You run it, answer a short guided onboarding, and it produces a complete video:

- **Minecraft gameplay** as a darkened, full-frame background (auto-fetched or your own upload)
- **Two animated cartoon avatars** in the bottom corners
- **AI-voiced dialogue** between the two characters, driven by an LLM-written script
- **Subtitles** showing who is speaking and what they say
- **Background music** at low volume, with fade-in and fade-out

No camera, no microphone, no editing. The entire thing is generated
programmatically from a single sentence describing your idea.

---

## Core philosophy

MinecraftCast is built as a **standalone tool first**. CROO integration is a
separate, optional layer on top.

This means:

- The **core pipeline works completely without CROO** — nothing in `pipeline.py`
  or `tools/` imports anything CROO-related.
- CROO is enabled with a single switch: `ENABLE_CROO=true` in `.env`.
- The tool can be dropped into any host afterward — MCP server, REST API, Discord
  bot, web app — because every external adapter lives in `integrations/` and
  wraps the same `pipeline.run(config)` entry point.

```
┌──────────────────────────────────────────────────────────┐
│                    integrations/  (optional)              │
│   croo_provider.py   rest_api.py   mcp_server.py          │
└───────────────┬──────────────┬──────────────┬────────────┘
                │              │              │
                ▼              ▼              ▼
        ┌──────────────────────────────────────────┐
        │        pipeline.run(VideoConfig)          │   ← zero external-platform deps
        │  script → footage → voice → avatar → mux  │
        └──────────────────────────────────────────┘
```

If you never touch CROO, you never load it. If you want it, flip one env var.

---

## Feature overview

| Capability            | Details                                                                                 |
|-----------------------|-----------------------------------------------------------------------------------------|
| **Script generation** | Groq `llama-3.3-70b-versatile`, strict JSON schema, self-correcting retries             |
| **Two characters**    | Independent names, personalities, voices, and avatar skins                              |
| **Voice: ElevenLabs** | 6 preset voices, emotion-mapped delivery, voice cloning from a sample                   |
| **Voice: Coqui XTTS**  | Fully local/offline, 6 preset speakers, zero-shot cloning from a WAV                     |
| **Avatars**           | South Park–style heads drawn with Pillow; 5 animation states; lip-sync mouth cycling     |
| **Footage**           | YouTube (`yt-dlp`) → Archive.org fallback → your own upload                              |
| **Subtitles**         | Per-segment speaker + text, drawn with FFmpeg `drawtext`                                 |
| **Music**             | Pixabay by mood, cached across jobs, silent-track fallback so assembly never fails      |
| **Lengths**           | Short (~2.5 min), Medium (~6 min), Long (~11 min)                                        |
| **Job tracking**      | SQLite (`minecraftcast.db`) via async `aiosqlite`                                        |
| **Integrations**      | REST API, MCP server, CROO provider — all optional                                      |

---

## The finished video

Output is a single **1920×1080 MP4** (H.264 video, AAC audio, `+faststart` for
instant web playback). The composition for every segment:

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│         Minecraft footage (full frame, darkened)        │
│                                                         │
│                                                         │
│   ┌──────────┐                        ┌──────────┐      │
│   │  CHAR 1  │                        │  CHAR 2  │      │
│   │  avatar  │                        │  avatar  │      │
│   │ 300×380  │                        │ 300×380  │      │
│   └──────────┘                        └──────────┘      │
│   x=40, y=640                         x=1580, y=640     │
│                                                         │
│   ═════════════════════════════════════════════════    │
│   [ CHAR NAME ]: dialogue text for this line…           │
│                                          (y = h − 60)   │
└─────────────────────────────────────────────────────────┘
```

**Speaking indicator:** the character currently talking is shown in full color;
the other is desaturated (via an FFmpeg `colorchannelmixer` matrix), so viewers
always know who has the mic. The speaker's mouth cycles through open/half-open
frames; the idle character holds still and blinks occasionally.

---

## Architecture at a glance

```
main.py
  ├─ ENABLE_CROO=false → onboarding.run_onboarding() → pipeline.run()
  └─ ENABLE_CROO=true  → integrations.croo_provider.run_croo_provider() → pipeline.run()

pipeline.run(VideoConfig)                     # the one true entry point
  1. tools/script.py        generate_script() → validated JSON script
  2. tools/footage.py       fetch_footage()   → local .mp4
  3. tools/voice_router.py  get_voice_engine()→ ElevenLabs | Coqui
        tools/voice_elevenlabs.py  |  tools/voice_coqui.py
  4. (voice).generate_all() → {segment_index: audio path}
  5. tools/avatar.py        AvatarGenerator   → per-segment avatar .mp4s
  6. tools/assembly.py      assemble_segment() ×N → concatenate_segments()
        tools/music.py      fetch()           → background track
  → output/{job_id}_final.mp4
```

Every stage prints a labeled `[n/6]` progress line to the terminal.

---

## Prerequisites

### System dependencies

| Tool         | Why it's needed                                              | Check                 |
|--------------|-------------------------------------------------------------|-----------------------|
| **FFmpeg**   | Footage slicing, audio transcode, avatar clips, final mux   | `ffmpeg -version`     |
| **FFprobe**  | Reading audio durations during assembly (ships with FFmpeg) | `ffprobe -version`    |
| **Python 3.11+** | Runtime (uses `str \| None` unions, `X \| Y` types)     | `python --version`    |
| **espeak-ng**    | Phonemizer for Coqui XTTS (only if using `coqui`)       | `espeak-ng --version` |
| **libsndfile1**  | Audio I/O for Coqui (Linux; only if using `coqui`)      | —                     |

> **FFmpeg must be on your `PATH`.** All media work shells out to `ffmpeg` /
> `ffprobe` via `subprocess` (the tool deliberately avoids the `ffmpeg-python`
> wrapper). The provided `Dockerfile` installs everything above for you.

### API keys

| Key                   | Required?                        | Used for                            |
|-----------------------|----------------------------------|-------------------------------------|
| `GROQ_API_KEY`        | **Yes**                          | Dialogue script generation          |
| `ELEVENLABS_API_KEY`  | Only if `voice_provider=elevenlabs` | ElevenLabs TTS + cloning         |
| `PIXABAY_API_KEY`     | Optional                         | Background music (silent fallback otherwise) |
| `CROO_SDK_KEY`        | Only if `ENABLE_CROO=true`       | CROO marketplace connection         |
| `CLOUDFLARE_R2_*`     | Only if `ENABLE_CROO=true`       | Uploading finished videos to R2     |

- **Groq** offers a generous free tier — get a key at <https://console.groq.com>.
- **ElevenLabs** is paid but highest quality — <https://elevenlabs.io>. Skip it
  entirely by choosing **Coqui** (free, local) during onboarding.
- **Pixabay** music key is free — <https://pixabay.com/api/docs/>.

---

## Installation

### 1. Clone and enter the project

```bash
git clone <your-repo-url> minecraftcast
cd minecraftcast
```

### 2. Install FFmpeg

<details>
<summary><strong>macOS</strong></summary>

```bash
brew install ffmpeg
```
</details>

<details>
<summary><strong>Ubuntu / Debian</strong></summary>

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libsndfile1 espeak-ng fonts-dejavu-core
```
</details>

<details>
<summary><strong>Windows</strong></summary>

```powershell
winget install Gyan.FFmpeg
# or: choco install ffmpeg
```
Then restart your terminal so `ffmpeg` is on `PATH`.
</details>

### 3. Create a virtual environment and install Python deps

```bash
python -m venv venv
# macOS/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

> **Heads up on `TTS` (Coqui):** it pulls in `torch`/`torchaudio` and is a large
> download. If you only plan to use ElevenLabs, you can comment out `TTS`,
> `torch`, and `torchaudio` in `requirements.txt` — they're imported lazily and
> never touched unless you pick the Coqui provider.

### 4. Configure your environment

```bash
cp .env.example .env
```

Open `.env` and fill in at least `GROQ_API_KEY`. Add `ELEVENLABS_API_KEY` if you
want ElevenLabs voices.

### 5. Run

```bash
python main.py
```

---

## Configuration (`.env` reference)

Every setting, grouped by concern. Values shown are the defaults from
`.env.example`.

```ini
# ── Core ─────────────────────────────────────────
GROQ_API_KEY=gsk_...            # REQUIRED — script generation

# ── Voice ────────────────────────────────────────
VOICE_PROVIDER=elevenlabs       # default suggestion; actual choice is per-run in onboarding
ELEVENLABS_API_KEY=             # required only when using ElevenLabs

# ── Optional: background music ───────────────────
PIXABAY_API_KEY=                # omit → silent background track is synthesized

# ── CROO Integration (optional layer) ────────────
ENABLE_CROO=false               # true → main.py runs as a CROO provider instead of the CLI
CROO_API_URL=https://api.croo.network
CROO_WS_URL=wss://api.croo.network/ws
CROO_SDK_KEY=croo_sk_...

# ── Storage (needed when ENABLE_CROO=true) ───────
CLOUDFLARE_ACCOUNT_ID=6d705cb729062c89c2f5d0a2b8c273cb
CLOUDFLARE_R2_ACCESS_KEY=
CLOUDFLARE_R2_SECRET_KEY=
CLOUDFLARE_R2_BUCKET=videoforge-outputs
CLOUDFLARE_R2_PUBLIC_DOMAIN=pub-XXXX.r2.dev

# ── Misc ─────────────────────────────────────────
LOG_LEVEL=INFO
```

| Variable                      | Default                     | Notes                                                                 |
|-------------------------------|-----------------------------|-----------------------------------------------------------------------|
| `GROQ_API_KEY`                | —                           | Required. Used by `tools/script.py`.                                  |
| `VOICE_PROVIDER`              | `elevenlabs`                | A hint; the real choice is made interactively per run.                |
| `ELEVENLABS_API_KEY`          | —                           | Required only for ElevenLabs. A clear error is raised if missing.     |
| `PIXABAY_API_KEY`             | —                           | Without it, a near-silent track is generated so assembly still works. |
| `ENABLE_CROO`                 | `false`                     | `true` switches `main.py` into CROO provider mode.                    |
| `CROO_API_URL` / `CROO_WS_URL`| CROO production endpoints   | Override for staging/local testing.                                   |
| `CROO_SDK_KEY`                | —                           | Required in CROO mode; startup aborts without it.                     |
| `CLOUDFLARE_ACCOUNT_ID`       | (sample)                    | R2 account for video hosting.                                         |
| `CLOUDFLARE_R2_ACCESS_KEY`    | —                           | R2 credentials.                                                       |
| `CLOUDFLARE_R2_SECRET_KEY`    | —                           | R2 credentials.                                                       |
| `CLOUDFLARE_R2_BUCKET`        | `videoforge-outputs`        | Target bucket name.                                                   |
| `CLOUDFLARE_R2_PUBLIC_DOMAIN` | —                           | Public dev URL for the bucket; falls back to `pub-<account[:8]>.r2.dev`. |
| `LOG_LEVEL`                   | `INFO`                      | Standard Python logging level.                                        |

---

## Usage — the interactive CLI

Run `python main.py` with `ENABLE_CROO=false` (the default). You'll see:

```
╔══════════════════════════════════════╗
║       MinecraftCast 🎮               ║
║  Faceless content, zero effort       ║
║  Type your idea. Get a YouTube video. ║
╚══════════════════════════════════════╝
```

Answer the six steps (detailed below), confirm, and the pipeline runs:

```
══════════════════════════════════════════════════
  JOB: 4b1f… 
══════════════════════════════════════════════════

[1/6] Generating script...
      ✓ 14 segments · 168.4s · "The Seed That Shouldn't Exist"
[2/6] Fetching Minecraft footage...
      ✓ /tmp/minecraftcast/4b1f…/footage.mp4
[3/6] Setting up voices...
      ✓ Alex: pNInz6obpgDQGcFmaJgB
      ✓ Steve: 21m00Tcm4TlvDq8ikWAM
[4/6] Generating dialogue audio...
      ✓ 14 audio clips
[5/6] Rendering avatars...
      ✓ Avatar animations done
[6/6] Assembling final video...
      ✓ output/4b1f…_final.mp4

══════════════════════════════════════════════════
  ✓ VIDEO READY
══════════════════════════════════════════════════
  File: output/4b1f…_final.mp4

  Upload directly to YouTube — it's ready.
══════════════════════════════════════════════════
```

The final file lands in **`output/`**, named `{job_id}_final.mp4`.

---

## The 6-step onboarding flow

`onboarding.py` collects a complete `VideoConfig` through plain `input()` prompts.

### Step 1 — Video topic
A free-text description of your video (minimum 10 characters). This is the
creative seed handed to the script model.
> *Example: "two guys react to the scariest Minecraft seeds ever"*

### Step 2 — Character 1
Name (default **Alex**) and a short personality description (minimum 5 chars).
> *Example personality: "sarcastic and funny, always doubting everything"*

### Step 3 — Character 2
Same as Step 2, default name **Steve**.

### Step 4 — Voice setup
First pick the **provider** for both characters:

```
[1] ElevenLabs  — Best quality, realistic voices (needs API key)
[2] Coqui       — Free, runs locally, good quality
```

Then, **per character**, you choose either:
- **Clone a voice** — paste the path to a 30–60s clear audio clip, or
- **Pick a preset** — from the provider's list (see [Voice system](#voice-system)).

Cloning paths are validated; if the file doesn't exist you're re-prompted or can
fall back to a preset.

### Step 5 — Avatar appearance
Per character, choose a skin:

```
[1] Steve      — Classic blue shirt      (#3B6BB5)
[2] Alex       — Green shirt             (#6AA84F)
[3] Creeper    — Green creeper face      (#4CAF50)
[4] Enderman   — Dark with purple eyes   (#1A1A1A)
[5] Custom     — Enter your own shirt hex color
```

### Step 6 — Length & footage
Length:

```
[1] Short  — 2-3 minutes   → 2.5 min
[2] Medium — 5-7 minutes   → 6.0 min
[3] Long   — 10-12 minutes → 11.0 min
```

Footage source:

```
[1] Auto-fetch from YouTube      (huge library, instant)
[2] Auto-fetch from Archive.org  (fully legal, no grey area)
[3] Upload my own footage        (drop file in uploads/ folder)
```

For YouTube/Archive you also name the gameplay **type** (e.g. `survival`,
`horror seeds`, `speedrun`, `caves`, `nether`, `building`). For upload, you drop a
file into `uploads/` and press Enter.

### Confirmation
A summary screen prints your choices and asks `Generate video? [y/n]`. Anything
other than `y` cancels.

---

## Voice system

Voices are routed by `tools/voice_router.get_voice_engine(provider)`, which lazily
imports only the engine you selected.

### ElevenLabs (`tools/voice_elevenlabs.py`)

- **Model:** `eleven_turbo_v2`
- **Narrator** segments use **Rachel** (`21m00Tcm4TlvDq8ikWAM`).
- **Emotion-aware delivery:** each segment's `emotion` maps to ElevenLabs
  `stability` / `similarity_boost` settings — e.g. `excited` → low stability
  (more expressive), `serious` → high stability (steadier).

**Preset voices:**

| Menu | Name   | Voice ID               | Character            |
|------|--------|------------------------|----------------------|
| 1    | Adam   | `pNInz6obpgDQGcFmaJgB` | Deep, authoritative male |
| 2    | Rachel | `21m00Tcm4TlvDq8ikWAM` | Warm, clear female   |
| 3    | Domi   | `AZnzlk1XvdvUeBnXmlld` | Energetic, young female |
| 4    | Bella  | `EXAVITQu4vr4xnSDxMaL` | Soft, warm female    |
| 5    | Antoni | `ErXwobaYiN019PkySvjV` | Well-rounded male    |
| 6    | Josh   | `TxGEqnHWrfWFTfGW9XjX` | Deep, serious male   |

**Emotion → settings map:**

| Emotion    | stability | similarity_boost |
|------------|-----------|------------------|
| excited    | 0.3       | 0.8              |
| scared     | 0.2       | 0.9              |
| skeptical  | 0.6       | 0.7              |
| laughing   | 0.2       | 0.8              |
| serious    | 0.7       | 0.8              |
| curious    | 0.4       | 0.8              |
| shocked    | 0.2       | 0.9              |
| smug       | 0.6       | 0.7              |
| *(default)*| 0.5       | 0.8              |

**Cloning:** if you provide a sample, `clone_voice()` uploads it to
`/voices/add` and uses the returned `voice_id`. A small rate-limit buffer sits
between segment requests.

### Coqui XTTS v2 (`tools/voice_coqui.py`)

- **Model:** `tts_models/multilingual/multi-dataset/xtts_v2`, loaded **once** at
  startup.
- **Fully local** — no network, no per-character cost.
- Output WAV is transcoded to MP3 so downstream stages see a uniform format.
- Synchronous synthesis runs in a thread pool to stay out of the async loop.

**Preset speakers:**

| Menu | Speaker           | Notes                |
|------|-------------------|----------------------|
| 1    | Claribel Dervla   | Female, clear (also the narrator) |
| 2    | Daisy Studious    | Female, professional |
| 3    | Gracie Wise       | Female, warm         |
| 4    | Tammie Ema        | Female, energetic    |
| 5    | Alison Dietlinde  | Deep                 |
| 6    | Ana Florence      | Calm                 |

**Cloning:** pass a reference WAV path; XTTS does zero-shot cloning via its
`speaker_wav` parameter — no training step, no uploaded voice.

---

## Avatar system

`tools/avatar.AvatarGenerator` draws everything **programmatically with Pillow** —
no external APIs, no asset packs.

### Skins & palettes

Each skin defines skin / hair / eye colors; the **shirt** color is whatever you
chose (preset or custom hex).

| Skin       | Skin      | Hair      | Eyes      |
|------------|-----------|-----------|-----------|
| `steve`    | `#C68642` | `#5C4033` | `#4A90D9` |
| `alex`     | `#C68642` | `#CB6D2A` | `#4A90D9` |
| `creeper`  | `#4CAF50` | `#388E3C` | `#000000` |
| `enderman` | `#1A1A1A` | `#1A1A1A` | `#9B59B6` |
| `custom`   | `#C68642` | `#5C4033` | `#4A90D9` |

Each avatar panel is **300×380 px** with a semi-transparent rounded background
and the character's name on a label bar at the bottom.

### Animation states

Five PNG frames are rendered per character:

| State       | Mouth          | Eyes  | Used when      |
|-------------|----------------|-------|----------------|
| `idle`      | closed         | open  | not speaking   |
| `talking_1` | slightly open  | open  | speaking cycle |
| `talking_2` | open (+ teeth)  | open  | speaking cycle |
| `talking_3` | slightly open  | open  | speaking cycle |
| `blink`     | closed         | blink | idle blink     |

### How motion is produced

`create_segment_video()` builds an FFmpeg `concat` list of frames at **8 fps**:

- **Speaking:** cycles `talking_1 → talking_2 → talking_3 → talking_2 …` for a
  simple lip-sync effect.
- **Idle:** holds `idle`, inserting a 2-frame `blink` roughly every 3 seconds.

Frames are encoded to a short per-segment MP4 (`libx264`, `ultrafast`).

---

## Footage system

`tools/footage.fetch_footage()` returns a single local video file, chosen by
`footage_source`.

### YouTube (`youtube`)
Uses **`yt-dlp`** to search `Minecraft {footage_type} gameplay no commentary`
and download the best ≤1080p MP4. A duration filter keeps clips between **5 and
60 minutes**. If YouTube fails for any reason, it **automatically falls back to
Archive.org**.

### Archive.org (`archive`)
Queries the Archive.org advanced-search API for `subject:(minecraft)` movies,
then walks the results until one yields a downloadable `.mp4`, streaming it to
disk in 1 MB chunks. Fully legal, public-domain / Creative-Commons material.

### Upload (`upload`)
Scans the **`uploads/`** folder for the first file with a video extension
(`.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`) and uses it. A clear error is raised if
the folder is empty.

> During assembly, each segment reads a **different 30-second window** of the
> source footage (`offset = segment_index × 30s`, wrapping within the first 10
> minutes) so the background doesn't visibly repeat.

---

## Music system

`tools/music.fetch(mood, job_id)` returns a local MP3 for the script's chosen
mood. Tracks are **cached by mood** under `/tmp/minecraftcast/music/` and reused
across jobs.

| Script mood   | Pixabay query               |
|---------------|-----------------------------|
| `chill`       | chill lofi ambient          |
| `tense`       | tension suspense dark       |
| `upbeat`      | upbeat energetic gaming     |
| `mysterious`  | mysterious ambient cinematic |

If `PIXABAY_API_KEY` is unset, a track can't be found, or a download fails, a
**near-silent MP3 is synthesized** with FFmpeg so the mix step always has a valid
input and the pipeline never breaks. In the final mux, music is applied at
**8% volume** with a 2s fade-in and a 3s fade-out.

---

## The pipeline, stage by stage

All of this lives in `pipeline.run(config)` and runs with **no CROO dependency**.

### 1 · Script — `tools/script.py`
Calls Groq (`llama-3.3-70b-versatile`) with a strict system prompt and returns
**pure JSON**. The response is:
- fence-stripped (in case the model wraps it in ```` ```json ````),
- parsed and **schema-validated** (`validate_script`),
- **normalized** (`_normalize_script`) — re-indexes segments, recomputes
  `word_count`, derives `duration_seconds` at ~150 wpm (`words × 0.4`, min 1.5s),
  recomputes the total duration, and defaults any missing optional fields.

On a JSON/validation error it **feeds the error back into the prompt and retries
up to 3 times**; on rate limits it backs off (10s, 20s, 30s).

### 2 · Footage — `tools/footage.py`
Fetches the background clip per the [Footage system](#footage-system).

### 3 · Voice setup — `tools/voice_router.py`
Resolves each character's voice: clones from a sample if provided, otherwise uses
the chosen preset (or a sensible default).

### 4 · Audio — voice engine `generate_all()`
Synthesizes **one audio clip per segment**, routing `char1` / `char2` / `narrator`
to the correct voice, and returns `{segment_index: audio_path}`.

### 5 · Avatars — `tools/avatar.py`
Renders the 5 state frames for each character, then a **per-segment avatar clip**
for both — animated for the speaker, idle+blink for the listener.

### 6 · Assembly — `tools/assembly.py`
For each segment, `assemble_segment()` builds an FFmpeg `filter_complex` that:
1. trims + scales the footage window to 1920×1080 and **darkens** it,
2. overlays both avatars (speaker full color, listener desaturated) at
   `x=40,y=640` and `x=1580,y=640`,
3. draws the **subtitle** (`Speaker: text`, escaped for `drawtext`, capped at 120 chars),
4. muxes the segment's dialogue audio.

Then `concatenate_segments()` concatenates all segment MP4s and **mixes in the
background music**, writing the final file to `output/{job_id}_final.mp4`.

Finally, the job's scratch directory under `/tmp/minecraftcast/{job_id}/` is
**deleted**.

---

## Data models

Defined in `config.py` (Pydantic v2). These are the single source of truth shared
by the CLI, the pipeline, and every integration.

```python
class CharacterConfig(BaseModel):
    name: str
    personality: str
    voice_provider: str                    # "elevenlabs" | "coqui"
    voice_id: Optional[str] = None         # preset voice ID / speaker name
    voice_sample_path: Optional[str] = None  # audio file for cloning
    avatar_skin: str = "steve"             # steve|alex|creeper|enderman|custom
    shirt_color: str = "#3B6BB5"           # hex

class VideoConfig(BaseModel):
    topic: str
    char1: CharacterConfig
    char2: CharacterConfig
    duration_minutes: float                # 2.5 | 6.0 | 11.0
    footage_source: str                    # youtube | archive | upload
    footage_type: str                      # survival | horror | speedrun | …
    job_id: str
```

---

## The script schema

`tools/script.py` instructs the model to return exactly this shape (and validates
the required fields before use):

```jsonc
{
  "title": "string",
  "youtube_title": "string",
  "total_duration_seconds": 0,
  "segments": [
    {
      "segment_index": 0,
      "type": "dialogue | narration",
      "speaker": "char1 | char2 | narrator",
      "text": "string",
      "word_count": 0,
      "duration_seconds": 0,
      "emotion": "excited | scared | skeptical | laughing | serious | curious | shocked | smug",
      "minecraft_action": "what's happening on screen right now",
      "footage_timestamp_hint": "0:30"
    }
  ],
  "footage_search_query": "string",
  "background_music_mood": "chill | tense | upbeat | mysterious",
  "thumbnail_segment_index": 0
}
```

**Validation rules enforced in code:**
- `title`, `total_duration_seconds`, `segments`, `footage_search_query`, and
  `background_music_mood` must be present.
- At least **3 segments**.
- Each segment's `speaker` ∈ {`char1`, `char2`, `narrator`} and its `text` is ≥ 5 chars.
- `background_music_mood` is coerced to `chill` if it isn't one of the four moods.

---

## Project structure

```
minecraftcast/
├── main.py                  # Entry point — CLI, or CROO provider when ENABLE_CROO=true
├── pipeline.py              # Core pipeline — ZERO CROO dependency
├── onboarding.py            # Interactive 6-step CLI flow
├── config.py                # Pydantic models (CharacterConfig, VideoConfig)
├── db.py                    # SQLite job tracking (aiosqlite)
│
├── tools/
│   ├── script.py            # Groq dialogue generator + schema validation
│   ├── footage.py           # yt-dlp + Archive.org + upload fetcher
│   ├── voice_router.py      # Picks the voice engine
│   ├── voice_elevenlabs.py  # ElevenLabs TTS + cloning + emotion mapping
│   ├── voice_coqui.py       # Coqui XTTS local TTS + cloning
│   ├── avatar.py            # South Park-style avatar generator (Pillow + FFmpeg)
│   ├── assembly.py          # FFmpeg compositing, concat, music mux
│   └── music.py             # Pixabay music fetcher + silent fallback
│
├── integrations/            # OPTIONAL external-platform adapters
│   ├── croo_provider.py     # CROO WebSocket provider
│   ├── rest_api.py          # FastAPI REST wrapper
│   └── mcp_server.py        # MCP server (stdio)
│
├── storage/
│   └── r2.py                # Cloudflare R2 upload (used by croo_provider)
│
├── assets/avatars/          # Reserved for optional avatar base images
├── uploads/                 # Drop your own Minecraft footage here
├── output/                  # Final MP4s land here
│
├── .env.example             # Copy to .env and fill in
├── requirements.txt
├── Dockerfile
└── README.md
```

Runtime scratch space lives at `/tmp/minecraftcast/{job_id}/` and is cleaned up
after each job.

---

## Integration layers

All three adapters live in `integrations/` and wrap the **same**
`pipeline.run(config)`. The core never imports them.

### REST API

FastAPI wrapper — good for Discord bots, web apps, or any HTTP client.

```bash
uvicorn integrations.rest_api:app --host 0.0.0.0 --port 8000
```

| Method & path      | Body / params                    | Returns                          |
|--------------------|----------------------------------|----------------------------------|
| `POST /generate`   | `GenerateRequest` (see below)    | `{ "job_id", "status": "queued" }` |
| `GET /job/{job_id}`| —                                | Job row (status, progress, output_url, error) |
| `GET /health`      | —                                | `{ "status": "ok", "service": "MinecraftCast" }` |

Generation runs in a **background task**; poll `/job/{job_id}` for status
(`queued → processing → complete` / `failed`).

**`GenerateRequest` fields (with defaults):**

```jsonc
{
  "topic": "…",                                  // required
  "char1_name": "Alex",
  "char1_personality": "energetic and funny",
  "char2_name": "Steve",
  "char2_personality": "calm and skeptical",
  "voice_provider": "elevenlabs",
  "duration_minutes": 3.0,
  "footage_source": "youtube",
  "footage_type": "survival gameplay"
}
```

**Example:**

```bash
# Queue a job
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
        "topic": "reacting to the scariest Minecraft seeds",
        "char1_personality": "sarcastic and skeptical",
        "char2_personality": "easily terrified",
        "duration_minutes": 3
      }'
# → {"job_id":"…","status":"queued"}

# Poll it
curl -s http://localhost:8000/job/<job_id>
```

### MCP server

Exposes MinecraftCast as a tool for MCP clients (Claude Desktop, Cursor, …).

```bash
python integrations/mcp_server.py
```

Tool: **`generate_minecraft_video`**

```jsonc
{
  "topic": "string",                 // required
  "char1_name": "Alex",
  "char1_personality": "string",     // required
  "char2_name": "Steve",
  "char2_personality": "string",     // required
  "duration_minutes": 3.0,
  "footage_type": "survival gameplay"
}
```

Returns `{"video_path": "output/…_final.mp4"}` as JSON text content.

### CROO Agent Store provider

Turn MinecraftCast into a paid, autonomous marketplace agent.

1. Set `ENABLE_CROO=true` in `.env`.
2. Fill in `CROO_SDK_KEY` and the `CLOUDFLARE_R2_*` storage settings.
3. Run `python main.py`.

The provider (`integrations/croo_provider.py`):
- connects to CROO over **WebSocket**,
- **auto-accepts** every incoming negotiation,
- on **`ORDER_PAID`**, parses the order's requirements into a `VideoConfig`, runs
  the pipeline, **uploads the MP4 to Cloudflare R2**, and **delivers the public
  URL** back via `deliver_order`,
- **rejects** the order with an error message if anything fails,
- **de-duplicates** in-flight orders so a reconnect can't run the same job twice,
- tracks each job in `minecraftcast.db`,
- shuts down cleanly on SIGINT/SIGTERM.

> **Robustness notes:** requirements are parsed defensively (they may arrive as a
> JSON string or dict under several possible field names), and the SDK is
> imported from its real top-level names (`from croo import …`).

---

## CROO dashboard service config

When registering on **agent.croo.network**, use these settings:

- **Service Name:** Generate Minecraft Video
- **Price:** 3.00 USDC
- **SLA:** 0h 45m
- **Description:** *"AI Minecraft faceless content creator. Two animated
  characters have an AI-voiced dialogue over Minecraft gameplay footage. Fully
  automated. Supports voice cloning, custom character personalities, and multiple
  video lengths."*

**Deliverable — Schema**

| field       | type   | format | required |
|-------------|--------|--------|----------|
| `video_url` | string | url    | yes      |

**Requirements — Schema**

| field               | type   | required | default            |
|---------------------|--------|----------|--------------------|
| `topic`             | string | yes      | —                  |
| `char1_name`        | string | no       | Alex               |
| `char1_personality` | string | yes      | —                  |
| `char2_name`        | string | no       | Steve              |
| `char2_personality` | string | yes      | —                  |
| `duration_minutes`  | number | no       | 3                  |
| `voice_provider`    | string | no       | elevenlabs         |
| `char1_voice_id`    | string | no       | —                  |
| `char2_voice_id`    | string | no       | —                  |
| `footage_source`    | string | no       | youtube            |
| `footage_type`      | string | no       | survival gameplay  |

**Skill tags:** `content-creation`, `video-generation`, `minecraft`,
`ai-voices`, `faceless-content`

---

## Docker

The image bundles `ffmpeg`, `libsndfile1`, `espeak-ng`, and DejaVu fonts —
everything the pipeline and Coqui XTTS need.

```bash
# Build
docker build -t minecraftcast .

# Run the CLI (mount output so finished videos land on your host)
docker run --rm -it \
  --env-file .env \
  -v "$PWD/output:/app/output" \
  -v "$PWD/uploads:/app/uploads" \
  minecraftcast
```

To run as a **CROO provider** instead, set `ENABLE_CROO=true` in your `.env` —
the same image and command will start the provider loop.

---

## Cost & performance

| Factor            | Impact                                                                       |
|-------------------|------------------------------------------------------------------------------|
| **Script (Groq)** | Fast and cheap; free tier is usually sufficient. One call per video (+retries). |
| **ElevenLabs**    | Billed per character of text. Longer videos = more segments = more audio.    |
| **Coqui**         | Free, but first run downloads the XTTS model and synthesis is CPU/GPU-bound.  |
| **Footage**       | Download time depends on source clip size and your connection.               |
| **Assembly**      | FFmpeg re-encodes each segment (`libx264`, `preset medium`) then concatenates. Scales with duration and segment count. |
| **Music**         | Cached per mood after the first fetch, so reused instantly across jobs.       |

Tips:
- Use **Coqui** to avoid per-character voice costs.
- Start with **Short** length while iterating on characters/topic.
- A GPU dramatically speeds up Coqui XTTS.

---

## Troubleshooting

**`ffmpeg: command not found` / assembly fails immediately**
FFmpeg isn't on your `PATH`. Install it (see [Installation](#installation)) and
restart your terminal. Verify with `ffmpeg -version` and `ffprobe -version`.

**`ELEVENLABS_API_KEY is not set`**
You chose ElevenLabs but didn't set the key. Add it to `.env`, or re-run and pick
**Coqui** (free, local).

**Script generation fails after 3 attempts**
The model returned invalid JSON three times. Check `GROQ_API_KEY` is valid and you
aren't rate-limited; the tool already retries with error feedback and backoff.

**No footage found**
For **upload**, make sure a video file is actually in `uploads/`. For
**YouTube/Archive**, try a more specific `footage_type`, or switch sources —
YouTube automatically falls back to Archive.org, but both can occasionally return
nothing for niche queries.

**Coqui / `TTS` import errors**
`TTS` (with `torch`/`torchaudio`) is a large, platform-sensitive install. If you
only use ElevenLabs, remove those three lines from `requirements.txt` — they're
imported lazily and never touched otherwise.

**No music in the final video**
Without `PIXABAY_API_KEY` (or on a failed fetch) a near-silent track is used by
design, so the mux never fails. Add the key for real background music.

**CROO provider exits with `CROO_SDK_KEY is not set`**
Set `CROO_SDK_KEY` in `.env` before running with `ENABLE_CROO=true`.

---

## FAQ

**Do I need both API keys?**
No. You always need `GROQ_API_KEY`. ElevenLabs is optional (use Coqui instead),
and Pixabay/CROO/R2 are optional depending on features.

**Can the two characters use different voices from different providers?**
Both characters share one provider per run (chosen in Step 4), but each can use a
different preset or its own cloned voice within that provider.

**Where do finished videos go?**
`output/{job_id}_final.mp4`. In CROO mode they're also uploaded to R2 and the
public URL is delivered to the buyer.

**Is my own footage modified?**
It's read-only as a source. The tool slices windows from it during assembly and
never overwrites your file in `uploads/`.

**Does it work offline?**
The script step needs Groq (network). Everything else can run locally if you use
Coqui voices and uploaded footage — though music will fall back to silence
without Pixabay.

---

## Extending MinecraftCast

The clean seams make it easy to add capabilities:

- **New voice engine** — implement a class with `setup_voice()`, `generate_all()`
  (and `generate_speech()`), then wire it into `tools/voice_router.get_voice_engine()`.
- **New footage source** — add a `_fetch_*` coroutine in `tools/footage.py` and a
  branch in `fetch_footage()`.
- **New integration** — add an adapter under `integrations/` that builds a
  `VideoConfig` and calls `pipeline.run()`. Follow `rest_api.py` / `mcp_server.py`.
- **New avatar style** — extend `AvatarGenerator._get_skin_colors()` and the
  `_draw_face()` shapes.
- **Different LLM** — the script client is a thin OpenAI-compatible wrapper; point
  it at another compatible endpoint/model.

Because `pipeline.py` has no external-platform dependency, you can embed the whole
thing anywhere Python runs.

---

## Legal & content notes

- Prefer **Archive.org** or **your own uploads** for footage you intend to
  monetize — they avoid the copyright grey area of arbitrary YouTube downloads.
- Respect the terms of service of every provider you enable (Groq, ElevenLabs,
  Pixabay, YouTube, Archive.org, CROO).
- Voice cloning should only be done with audio you have the rights to use.
- You are responsible for the content you generate and publish.

---

<div align="center">

Made for faceless creators. Type an idea, get a video. 🎮

</div>
