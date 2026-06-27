FROM python:3.11-slim

# System deps: ffmpeg (assembly), libsndfile1 (audio I/O), espeak-ng (Kokoro G2P),
# fonts (drawtext/subtitles overlays).
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

CMD ["python", "main.py"]
