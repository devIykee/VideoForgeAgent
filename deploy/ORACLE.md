# Hosting MinecraftCast on Oracle Cloud (Always Free)

This runs the CROO provider (or the REST API) 24/7 on an Oracle **Always Free**
Arm VM at no cost. Total time: ~30–40 min.

Why Oracle Ampere A1 and not the AMD micro: video encoding with FFmpeg needs
CPU + RAM. The Always-Free **Ampere A1** gives you up to 4 OCPU / 24 GB RAM for
free; the AMD `E2.1.Micro` only has 1 GB RAM and will choke on renders.

---

## 0. What you need
- Oracle Cloud account (credit card for identity verification — Always Free is
  never charged; you can even leave "Upgrade to Paid" off).
- Your API keys: `GROQ_API_KEY` (required). For CROO mode also `CROO_SDK_KEY`
  and Cloudflare R2 keys. `PIXABAY_API_KEY` optional (silent music fallback works
  without it).
- An SSH key pair (the guide creates one).

---

## 1. Create the Oracle account
1. Go to https://www.oracle.com/cloud/free/ → **Start for free**.
2. Verify email, enter card (identity only — no charge on Always Free).
3. Pick a **Home Region** close to you *that has Ampere capacity* (Ashburn,
   Phoenix, Frankfurt, London usually do). You cannot change this later.

---

## 2. Launch an Always-Free Arm instance
1. Console → hamburger menu → **Compute → Instances → Create instance**.
2. **Name:** `minecraftcast`.
3. **Image:** Change image → **Canonical Ubuntu 22.04**.
4. **Shape:** Change shape → **Ampere** → `VM.Standard.A1.Flex` →
   set **4 OCPUs, 24 GB RAM** (the full Always-Free allotment).
   - If you see "Out of capacity", try again later or pick 1 OCPU / 6 GB, or a
     different home region. Capacity comes and goes.
5. **Networking:** leave defaults (creates a VCN + public IP).
6. **SSH keys:** choose **Generate a key pair for me** → **Save private key**
   (you'll get `ssh-key-*.key`). Also save the public key.
7. **Create.** Wait until state = **Running**, then copy the **Public IP**.

---

## 3. Open the firewall (ONLY if you run the REST API)
CROO mode needs **no inbound ports** — the provider dials *out* over WebSocket.
Skip this section for CROO. For the REST API on port 8000:
1. Instance → **Virtual Cloud Network** → **Security Lists** → default list.
2. **Add Ingress Rule:** Source `0.0.0.0/0`, IP Protocol TCP, Dest port `8000`.
3. On the VM you'll also open the OS firewall (step 5 note).

---

## 4. SSH in
On Windows (Git Bash / PowerShell), from where you saved the key:
```bash
chmod 600 ssh-key-*.key            # Git Bash only
ssh -i ssh-key-*.key ubuntu@<PUBLIC_IP>
```
(Default user for Ubuntu images is `ubuntu`.)

---

## 5. Install Docker on the VM
```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
newgrp docker                      # apply group without re-login
docker --version                   # sanity check
```

---

## 6. Get the code onto the VM
Option A — clone from GitHub (recommended; push your repo first):
```bash
git clone https://github.com/<you>/minecraftcast.git
cd minecraftcast
```
Option B — copy from your PC with scp (run on your PC, not the VM):
```bash
scp -i ssh-key-*.key -r "C:/IykeStuff/Coding/videoforge" ubuntu@<PUBLIC_IP>:~/minecraftcast
```
> Don't copy `.venv/`, `output/`, `uploads/`, `__pycache__/` — they're big and
> rebuilt on the server. `.gitignore` already excludes them for the git route.

---

## 7. Configure secrets
```bash
cp .env.example .env
nano .env
```
Set at minimum:
```
GROQ_API_KEY=gsk_...
VOICE_PROVIDER=edge          # free, no key
ENABLE_CROO=true             # false if you just want the REST API / CLI

# CROO mode also needs:
CROO_SDK_KEY=croo_sk_...
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_R2_ACCESS_KEY=...
CLOUDFLARE_R2_SECRET_KEY=...
CLOUDFLARE_R2_BUCKET=...
CLOUDFLARE_R2_PUBLIC_DOMAIN=pub-XXXX.r2.dev
```
Save: Ctrl+O, Enter, Ctrl+X.

---

## 8. Build & run (CROO provider)
```bash
docker compose -f deploy/docker-compose.yml up -d --build
```
First build takes ~5–10 min (installs ffmpeg + Python deps). Then:
```bash
docker compose -f deploy/docker-compose.yml logs -f      # watch it connect
```
You want to see the provider connect to the CROO WebSocket and wait for orders.
`restart: unless-stopped` brings it back after crashes/reboots.

Stop / update:
```bash
docker compose -f deploy/docker-compose.yml down
git pull && docker compose -f deploy/docker-compose.yml up -d --build
```

---

## 8b. Alternative: run the REST API instead of CROO
If your (non-CROO) submission wants an HTTP endpoint, run `integrations/rest_api.py`
with uvicorn. Quick version without compose:
```bash
docker build -t minecraftcast .
docker run -d --name mc-rest --env-file .env -p 8000:8000 \
  minecraftcast uvicorn integrations.rest_api:app --host 0.0.0.0 --port 8000
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save        # keep the rule after reboot
```
Then hit `http://<PUBLIC_IP>:8000/` (do step 3 ingress first).

---

## 9. Keep-alive & health
- `docker ps` — container should say `Up`.
- Reboots: `restart: unless-stopped` auto-starts the container on VM boot.
- Disk: renders land in `./output` (mounted volume). Clear old files
  periodically: `find output -name '*.mp4' -mtime +7 -delete`.
- Logs filling disk: `docker compose ... logs --tail=100`.

---

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Out of host capacity" on create | Retry, lower to 1 OCPU/6 GB, or change home region |
| `docker: permission denied` | Re-run `newgrp docker` or log out/in |
| Build OOM-killed | You picked the 1 GB AMD micro — recreate as Ampere A1 |
| FFmpeg font/subtitle error | Dockerfile installs `fonts-dejavu-core`; rebuild |
| Provider won't connect | Check `CROO_SDK_KEY` and outbound 443 allowed |
| Render slow | Normal: ~5 min for ~1 min of video on 4 OCPU |
