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

CMD ["python", "main.py"]
