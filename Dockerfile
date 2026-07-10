FROM python:3.11-slim

# System deps: ffmpeg (footage/audio/assembly), libsndfile1 (Coqui audio I/O),
# espeak-ng (Coqui XTTS phonemizer), fonts (drawtext subtitle/label overlays).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        espeak-ng \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable scratch/output dirs (some PaaS run the container as a non-root user).
# The DB lives in /tmp so it's writable regardless of the runtime UID.
RUN mkdir -p /app/output /app/uploads /tmp/minecraftcast \
    && chmod -R 777 /app/output /app/uploads /tmp/minecraftcast
ENV MINECRAFTCAST_DB_PATH=/tmp/minecraftcast.db

# Default container mode = REST API. The app binds to $PORT (see main.py);
# platforms like Cloud Run inject PORT (default 8080), so we don't pin it here.
# ENABLE_CROO=true (set by the CROO compose file) takes precedence at runtime,
# so SERVE_REST is harmless for the CROO deployment.
ENV SERVE_REST=true
EXPOSE 8080

CMD ["python", "main.py"]
