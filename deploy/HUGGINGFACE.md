# Hosting MinecraftCast on Hugging Face Spaces (Free)

Runs the REST API 24/7 on a free **CPU basic** Space (16 GB RAM / 2 vCPU) — no
credit card, no capacity lottery. The Space's public URL is your live demo:
judges POST a topic and download the finished MP4. Total time: ~20 min.

Why HF over Render/Koyeb/GCP micro for *this* app: MinecraftCast is CPU- and
RAM-heavy (FFmpeg encodes 1080p H.264). The 512 MB / 0.1-vCPU PaaS tiers can't
encode video in reasonable time; HF's 16 GB / 2 vCPU can.

---

## 0. What you need
- A free Hugging Face account: https://huggingface.co/join
- Your `GROQ_API_KEY` (from https://console.groq.com). That's the only required
  secret — voice (edge-tts) and the silent-music fallback need no keys.
- Git installed locally.

---

## 1. Create the Space
1. https://huggingface.co/new-space
2. **Owner/name:** e.g. `minecraftcast`.
3. **License:** MIT.
4. **SDK:** select **Docker** → **Blank**.
5. **Hardware:** **CPU basic · 2 vCPU · 16 GB · FREE**.
6. **Visibility:** Public (required for the free tier / uptime pings).
7. **Create Space.** You now have a git repo at
   `https://huggingface.co/spaces/<user>/minecraftcast`.

---

## 2. Add your Groq key as a secret
Space → **Settings** → **Variables and secrets** → **New secret**:
- Name: `GROQ_API_KEY`
- Value: `gsk_...`

(Secrets are injected as env vars at runtime — never commit `.env`.)

Optional secret: `PIXABAY_API_KEY` (music; silent fallback works without it).

---

## 3. Push the code to the Space
The Dockerfile already defaults to REST mode on port 7860, so no code changes
are needed. From your repo root:

```bash
# HF needs a README.md with Space front matter — use the one we prepared:
cp deploy/SPACE_README.md README.hf.md      # keep your real README intact locally

# Add the Space as a git remote (get the URL from the Space page):
git remote add space https://huggingface.co/spaces/<user>/minecraftcast

# Authenticate: when git prompts for a password, paste a HF *access token*
# with WRITE scope (https://huggingface.co/settings/tokens), username = HF user.
```

Because HF reads `README.md`, the simplest reliable push is a dedicated branch
whose `README.md` is the Space card:

```bash
git checkout -b hf-space
cp deploy/SPACE_README.md README.md          # overwrite ONLY on this branch
git add README.md && git commit -m "HF Space card"
git push space hf-space:main                  # push this branch as the Space's main
git checkout main                             # back to your normal branch
```

> `.gitignore` already excludes `.env`, `venv/`, `output/`, `*.mp4` — so no
> secrets or large media get pushed, and you won't need git-lfs.

---

## 4. Watch it build
On the Space page, open the **Logs** tab. HF builds the Dockerfile
(installs ffmpeg + Python deps, ~5–8 min), then starts uvicorn. When you see
`Uvicorn running on http://0.0.0.0:7860`, it's live.

Your app is at: `https://<user>-minecraftcast.hf.space`
Interactive docs: `https://<user>-minecraftcast.hf.space/docs`

---

## 5. Test it
```bash
BASE=https://<user>-minecraftcast.hf.space

curl -s $BASE/health

# Queue a short video (keep demos ~1–1.5 min; ~5–8 min render on 2 vCPU):
curl -s -X POST $BASE/generate -H "Content-Type: application/json" \
  -d '{"topic":"the scariest Minecraft seeds","duration_minutes":1.5,"footage_source":"archive"}'
# -> {"job_id":"...","status":"queued"}

curl -s $BASE/job/<job_id>                    # poll until "status":"complete"
curl -sL $BASE/download/<job_id> -o video.mp4 # fetch the MP4
```
> Tip: `footage_source":"archive"` (Archive.org) is more reliable from a
> datacenter IP than YouTube, which may rate-limit yt-dlp.

---

## 6. Keep it awake (beat the 48-hour sleep)
Free Spaces pause after 48 h of no traffic. A single daily ping keeps it up:
1. Create a free account at https://uptimerobot.com (or cron-job.org).
2. New monitor → **HTTP(s)** → URL = `https://<user>-minecraftcast.hf.space/health`
   → interval every 12 hours (well under 48 h).
That's it — the Space stays warm indefinitely.

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| Build fails on `torch` | You uncommented Coqui in requirements.txt — re-comment it; edge-tts needs no torch |
| App "unhealthy" / no port | Confirm `app_port: 7860` in the Space README front matter and the build log shows uvicorn on 7860 |
| `GROQ ... missing credentials` in job error | Add `GROQ_API_KEY` as a Space **secret** (step 2), then Restart the Space |
| Job stuck `processing` a long time | Normal for 3-min videos on 2 vCPU; use `duration_minutes` 1–1.5 for demos |
| Video 404 on `/download` | Storage is ephemeral — fetch it before the Space restarts/rebuilds |
| Push rejected (auth) | Use a HF **write** token as the git password, not your account password |
