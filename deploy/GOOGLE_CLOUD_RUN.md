# Hosting MinecraftCast on Google Cloud Run

Cloud Run runs your Docker container and **scales to zero** — you pay only while
a video is actually rendering. With the free monthly tier (180k vCPU-seconds +
360k GiB-seconds) that's roughly **~90 short renders/month for $0**, then a few
cents per render after. No always-on server, no credit-card ban games.

**Important — use the `POST /render` endpoint, not `/generate`.** Cloud Run only
gives your container CPU *during an active request*. `/generate` renders in a
background task that Cloud Run would freeze after responding. `/render` does the
work inside the request, which is exactly what Cloud Run wants. (The Dockerfile
and app are already set up for this.)

Total setup time: ~20 min.

---

## 0. What you need
- A Google account + the **gcloud CLI**: https://cloud.google.com/sdk/docs/install
- A billing account linked to the project. Cloud Run's free tier still requires
  billing enabled for identity, but you stay within free limits at low volume.
- Your `GROQ_API_KEY` (https://console.groq.com). Voice (edge-tts) needs no key.

---

## 1. One-time gcloud setup
```bash
gcloud auth login
gcloud projects create minecraftcast-$RANDOM --name="MinecraftCast"   # or reuse one
gcloud config set project <YOUR_PROJECT_ID>

# Link billing (needed even for free tier). List accounts, then link:
gcloud billing accounts list
gcloud billing projects link <YOUR_PROJECT_ID> --billing-account=<ACCOUNT_ID>

# Enable the services we use:
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

> If you run any `gcloud auth login` / interactive login yourself, type it in
> this session prefixed with `!` so the output lands here.

---

## 2. Store the Groq key as a secret (recommended)
```bash
printf 'gsk_your_real_key' | gcloud secrets create GROQ_API_KEY --data-file=-
# grant Cloud Run's default runtime service account access:
PROJECT_NUMBER=$(gcloud projects describe <YOUR_PROJECT_ID> --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding GROQ_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```
(Simpler but less secure alternative: skip this and pass
`--set-env-vars GROQ_API_KEY=gsk_...` in the deploy command below.)

---

## 3. Deploy (builds the Dockerfile automatically)
From the repo root:
```bash
gcloud run deploy minecraftcast \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --cpu 4 \
  --memory 8Gi \
  --timeout 3600 \
  --concurrency 1 \
  --max-instances 2 \
  --min-instances 0 \
  --set-secrets GROQ_API_KEY=GROQ_API_KEY:latest
```
What each flag does for this workload:
- `--source .` — Cloud Build builds your `Dockerfile` and pushes the image (no
  Docker needed locally). First build ~5–8 min.
- `--cpu 4 --memory 8Gi` — headroom for 1080p x264. **Cloud Run's filesystem is
  in-memory**, so downloaded footage + scratch files count against RAM; 8 Gi is
  a safe cushion. Per-second billing means a big instance for 8 min still costs
  pennies.
- `--timeout 3600` — allow up to 60 min per request (renders are well under).
- `--concurrency 1` — one render per instance (it's CPU-bound).
- `--max-instances 2` — cap cost; renders won't fan out unbounded.
- `--min-instances 0` — scale to zero when idle = you pay nothing between renders.

When it finishes it prints a **Service URL** like
`https://minecraftcast-xxxx-uc.a.run.app`.

---

## 4. Test it
```bash
URL=https://minecraftcast-xxxx-uc.a.run.app

curl -s $URL/health
# {"status":"ok","service":"MinecraftCast"}

# Synchronous render — waits ~5–8 min, then streams back the MP4:
curl -s -X POST $URL/render \
  -H "Content-Type: application/json" \
  -d '{"topic":"the scariest Minecraft seeds","duration_minutes":1.5,"footage_source":"archive"}' \
  --max-time 1800 -o video.mp4

ls -lh video.mp4        # your finished video
```
Interactive docs (great for a judge demo): open `$URL/docs` in a browser and
call `POST /render` from there.

> Use `"footage_source":"archive"` (Archive.org) — more reliable from a Google
> datacenter IP than YouTube, which may rate-limit yt-dlp.
> Keep `duration_minutes` around 1–1.5 for demos: shorter render, smaller
> response (Cloud Run streams the file back over the request).

---

## 5. Cost & scaling notes
- **Free tier:** 180k vCPU-sec + 360k GiB-sec + 2M requests / month. A ~1.5-min
  video (~8 min at 4 vCPU / 8 GiB) uses ~1,920 vCPU-sec and ~3,840 GiB-sec, so
  the free tier covers **roughly 90 renders/month**. Beyond that it's about
  **2–5 cents per render**.
- **Idle = $0.** With `--min-instances 0` you pay nothing when no one is
  rendering. First request after idle has a ~10–20 s cold start.
- Watch spend: **Billing → Budgets & alerts** → set a $1–5 alert so there are no
  surprises.

---

## 6. Updating the service
```bash
git pull        # or make changes
gcloud run deploy minecraftcast --source . --region us-central1   # redeploys
```
Flags from the first deploy are remembered; you only re-pass ones you change.

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| Deploy: "billing account required" | Link billing (step 1); free tier still needs it enabled |
| Job/render 500 with "missing credentials" | Groq key not wired — check the `--set-secrets` binding or use `--set-env-vars GROQ_API_KEY=...` |
| Render killed / OOM | Raise `--memory` (footage lives in RAM on Cloud Run); try 12–16Gi or a shorter/`upload` footage source |
| "Container failed to start / listen on PORT" | The app binds `$PORT` automatically; don't set PORT yourself. Redeploy without a custom `--port` |
| Response truncated for long videos | Cloud Run streams responses, but keep demos short; for big files upload to R2 and return a URL instead |
| Cold start feels slow | Normal (~10–20 s). Set `--min-instances 1` for instant response, but that bills ~24/7 |

---

## CROO provider on Cloud Run?
Cloud Run is request-driven, so it's a poor fit for the CROO provider's
*persistent outbound WebSocket* (it needs CPU allocated continuously). For CROO,
use an always-on host (a small VPS like Hetzner, ~$4/mo) with
`deploy/docker-compose.yml` and `ENABLE_CROO=true`. Cloud Run is for the REST
(`/render`) demo.
