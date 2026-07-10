# Deploying the MinecraftCast provider

The provider is a **long-running process** that connects out to CROO over
WebSocket. It only accepts orders while running, so for a live listing you want
it up 24/7. Pick one option below.

> **One process per API key.** The CROO SDK permits a single active WebSocket
> connection per key — never run two copies with the same `CROO_SDK_KEY`.

Prerequisites for every option: a filled-in `.env` (with `ENABLE_CROO=true`,
`CROO_SDK_KEY`, `GROQ_API_KEY`, and the `CLOUDFLARE_R2_*` vars), and `ffmpeg`
available (the Docker image installs it for you).

---

## Option A — systemd (bare VPS)

Best for a plain Ubuntu/Debian box.

```bash
# 1. Put the code somewhere stable and build a venv
sudo git clone https://github.com/devIykee/VideoForgeAgent.git /opt/minecraftcast
cd /opt/minecraftcast
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
sudo apt-get install -y ffmpeg libsndfile1 espeak-ng fonts-dejavu-core

# 2. Create .env (ENABLE_CROO=true + all keys)
cp .env.example .env && nano .env

# 3. Install the service (edit User/paths in the unit first if needed)
sudo cp deploy/minecraftcast.service /etc/systemd/system/minecraftcast.service
sudo systemctl daemon-reload
sudo systemctl enable --now minecraftcast

# 4. Watch it connect and go Online
journalctl -u minecraftcast -f
```

---

## Option B — Docker Compose (any host with Docker)

```bash
cp .env.example .env && nano .env      # ENABLE_CROO=true + keys
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f
```

Finished videos land in `./output`; the SQLite job DB persists in the `mc-db`
volume.

---

## Option C — tmux (quick demo / temporary)

Fine for the hackathon demo; not durable across reboots.

```bash
tmux new -s minecraftcast
export ENABLE_CROO=true
python main.py
# detach: Ctrl-b then d   |   reattach: tmux attach -t minecraftcast
```

---

## Verifying it's live

1. In the Agent Store dashboard, your agent's status shows **Online**.
2. Provider logs print `MinecraftCast CROO provider online ✓ — waiting for orders...`
3. Place a test order (second agent or the Store UI) and watch the logs flow
   `NEGOTIATION_CREATED → accept → ORDER_PAID → pipeline → deliver_order → order_completed`.

If the status stays **draft**, the WebSocket handshake didn't complete — check
`CROO_SDK_KEY`, `CROO_WS_URL`, and that no other process holds the same key.
