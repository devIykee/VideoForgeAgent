# MinecraftCast — CROO Agent Hackathon Submission

**Track(s):** Creator & Content Ops Agents (primary) · Open – Any A2A Agents (secondary)
**License:** MIT (see [`LICENSE`](./LICENSE))
**Repo:** https://github.com/devIykee/VideoForgeAgent  *(public)*
**Demo video:** _<paste YouTube/unlisted link before filing>_

---

## One-line pitch

A paid, callable agent that turns a single sentence into a finished, upload-ready
faceless Minecraft YouTube video — two AI-voiced cartoon characters reacting over
gameplay footage — delivered on-chain as a public video URL.

---

## What it does

`MinecraftCast` is a fully automated video-generation service. A buyer (human on
the Agent Store, or another agent via A2A) submits a topic and two character
personalities; the agent produces a 1920×1080 MP4 and returns its URL.

Pipeline (all in `pipeline.py`, zero CROO dependency):

1. **Script** — Groq `llama-3.3-70b-versatile` writes a validated JSON dialogue script
2. **Footage** — Minecraft gameplay via yt-dlp → Archive.org fallback → upload
3. **Voice** — ElevenLabs or local Coqui XTTS (presets + cloning), per segment
4. **Avatars** — South Park–style talking heads drawn with Pillow, animated per segment
5. **Assembly** — FFmpeg composites footage + avatars + subtitles, concatenates, mixes music

---

## How CAP / the CROO SDK is integrated

The core tool is standalone; CROO is a thin optional layer enabled with
`ENABLE_CROO=true`. All integration lives in **`integrations/croo_provider.py`**,
which wraps `pipeline.run()`.

### Architecture

```
Buyer / Requester agent          YOUR provider process (main.py, ENABLE_CROO=true)
        │                        ┌─────────────────────────────────────────────┐
        │  order + pay (USDC)    │  connect_websocket()  ──►  wss://api.croo… /ws │
        ▼                        │  on NEGOTIATION_CREATED → accept_negotiation   │
   CROO backend ── order_paid ──►│  on ORDER_PAID → get_order → pipeline.run()    │
        ▲                        │  upload MP4 → Cloudflare R2                     │
        │  video_url             │  deliver_order(SCHEMA{video_url})  ──►          │
        └────────────────────────┴─────────────────────────────────────────────┘
```

The agent is **not a website** — it is a long-running process that makes an
outbound WebSocket connection to CROO. When connected, its status flips to
**Online** in the dashboard and it becomes discoverable/orderable. Hosting is
just "keep this process running" (see [Hosting](#hosting)).

### SDK symbols used (`croo-sdk`, Python)

| Symbol | Where | Purpose |
|--------|-------|---------|
| `Config(base_url, ws_url)` | provider startup | Point the client at CROO API + WS endpoints |
| `AgentClient(config, api_key)` | provider startup | Authenticated client (API key from dashboard) |
| `client.connect_websocket()` | provider startup | Open the event stream; brings agent Online |
| `EventType.NEGOTIATION_CREATED` | `stream.on(...)` | Trigger to auto-accept incoming negotiations |
| `EventType.ORDER_PAID` | `stream.on(...)` | Trigger to run the pipeline for a paid order |
| `client.accept_negotiation(negotiation_id)` | on negotiation | Accept → CROO dual-signs & creates the on-chain Order |
| `client.get_order(order_id)` | on paid | Fetch order + its requirements JSON |
| `client.deliver_order(order_id, DeliverOrderRequest(...))` | after render | Submit the deliverable → settlement |
| `DeliverableType.SCHEMA` | delivery | Structured JSON deliverable |
| `DeliverOrderRequest(deliverable_type, deliverable_schema)` | delivery | Carries `{video_url, title, duration_minutes, status}` |
| `client.reject_order(order_id, reason)` | on failure | Reject + refund if the pipeline fails |
| `client.close()` / `stream.close()` | shutdown | Clean disconnect on SIGINT/SIGTERM |
| `APIError`, `is_insufficient_balance`, `is_not_found` | error handling | Typed error branches |

### Order lifecycle handled

```
NEGOTIATION_CREATED ─► accept_negotiation ─► (order_created) ─► ORDER_PAID
      ─► get_order ─► pipeline.run() ─► upload to R2 ─► deliver_order ─► order_completed
                                   └─ on any failure ─► reject_order (refund)
```

### Robustness notes (relevant to the human spot-check)

- **Idempotency:** in-flight `order_id`s are tracked in a set so a WS reconnect or
  duplicate event can't run the same job twice.
- **Defensive requirements parsing:** order requirements may arrive as a JSON
  string or dict under several field names; `_parse_requirements()` probes and
  falls back safely.
- **Single WS connection:** the SDK allows one active WebSocket per API key — run
  exactly one provider process per key.
- **Graceful shutdown:** SIGINT/SIGTERM closes the stream and client cleanly.
- **Job tracking:** every order is recorded in `minecraftcast.db` (SQLite).

---

## Service configuration (Agent Store dashboard)

| Field | Value |
|-------|-------|
| Service Name | Generate Minecraft Video |
| Price | 3.00 USDC |
| SLA | 0h 45m |
| Deliverable | **Schema** → `video_url` (string, url, required) |
| Requirements | **Schema** (see table below) |
| Skill Tags | content-creation, video-generation, minecraft, ai-voices, faceless-content |

**Requirements schema**

| field | type | required | default |
|-------|------|----------|---------|
| `topic` | string | yes | — |
| `char1_name` | string | no | Alex |
| `char1_personality` | string | yes | — |
| `char2_name` | string | no | Steve |
| `char2_personality` | string | yes | — |
| `duration_minutes` | number | no | 3 |
| `voice_provider` | string | no | elevenlabs |
| `char1_voice_id` | string | no | — |
| `char2_voice_id` | string | no | — |
| `footage_source` | string | no | youtube |
| `footage_type` | string | no | survival gameplay |

**Deliverable payload example**

```json
{
  "video_url": "https://pub-xxxx.r2.dev/videos/<job_id>/output.mp4",
  "title": "reacting to the scariest Minecraft seeds",
  "duration_minutes": 3.0,
  "status": "complete"
}
```

---

## Run it (provider / CROO mode)

```bash
pip install -r requirements.txt
cp .env.example .env      # fill GROQ_API_KEY, CROO_SDK_KEY, CLOUDFLARE_R2_*, (ELEVENLABS_API_KEY)

export ENABLE_CROO=true   # or set in .env
python main.py            # connects to CROO, agent goes Online, waits for orders
```

Standalone CLI mode (no CROO) for local testing: leave `ENABLE_CROO=false` and run
`python main.py` — it walks the onboarding flow and writes an MP4 to `output/`.

### Required env for CROO mode

```bash
CROO_API_URL=https://api.croo.network
CROO_WS_URL=wss://api.croo.network/ws
CROO_SDK_KEY=croo_sk_...            # from dashboard, shown once
GROQ_API_KEY=gsk_...                # script generation
ELEVENLABS_API_KEY=...              # only if voice_provider=elevenlabs
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_R2_ACCESS_KEY=...
CLOUDFLARE_R2_SECRET_KEY=...
CLOUDFLARE_R2_BUCKET=...
CLOUDFLARE_R2_PUBLIC_DOMAIN=pub-xxxx.r2.dev
```

> Delivery returns a **public URL**, so Cloudflare R2 (or any public bucket) is
> required in CROO mode. Enable the bucket's Public Development URL and copy the
> exact domain into `CLOUDFLARE_R2_PUBLIC_DOMAIN`.

---

## Hosting

The provider only accepts orders while the process is running. Options:

- **Local machine** — fine for the demo; agent goes offline when you close it.
- **VPS (recommended for 24/7)** — run under `systemd`, `tmux`, or Docker so it
  survives disconnects. See [`deploy/`](./deploy) for a `systemd` unit and a
  `docker-compose.yml`.

One provider process per API key (SDK enforces a single active WS connection).

---

## Submission checklist

- [x] Open source, MIT license
- [x] CAP integration coded (`integrations/croo_provider.py`)
- [x] README with setup, SDK methods, integration notes
- [ ] Repo made **public**
- [ ] Agent **registered + service configured** on agent.croo.network
- [ ] Cloudflare R2 configured (delivery URL)
- [ ] Provider run live → agent **Online**
- [ ] ≥1 real order completed on-chain (`order_paid → order_completed`)
- [ ] Reward-eligibility: aim for ≥3 unique counterparty agents / ≥5 unique buyer wallets
- [ ] Demo video recorded (≤5 min) and linked above
- [ ] BUIDL filed on DoraHacks before **2026-07-12 10:00**

---

## Demo video shot list (≤5 min)

1. **0:00–0:30 — Hook.** One sentence: "MinecraftCast turns a single prompt into a
   finished faceless Minecraft video, and sells it as a CROO agent." Show the Agent
   Store listing.
2. **0:30–1:30 — Standalone proof.** Run `python main.py` (CLI mode), speed through
   onboarding, show the `[1/6]…[6/6]` pipeline log, open the finished MP4 — point out
   avatars, subtitles, speaker highlighting, music.
3. **1:30–2:15 — Go Online.** Set `ENABLE_CROO=true`, `python main.py`; show the
   dashboard status flip **draft → Online**.
4. **2:15–4:00 — Real order end-to-end.** From a second agent / the Store UI, order
   "Generate Minecraft Video" and pay USDC. Show the provider logs:
   `NEGOTIATION_CREATED → accept → ORDER_PAID → pipeline → deliver_order → order_completed`.
   Open the delivered `video_url`.
5. **4:00–4:45 — Why it matters.** A2A composability: any other agent (a "channel
   manager", a "thumbnail maker") can hire this as a dependency. On-chain settlement,
   no manual steps.
6. **4:45–5:00 — Close.** Repo + license + track.
