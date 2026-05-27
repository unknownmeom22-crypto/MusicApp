# MusicApp backend — production Dockerfile
#
# Build:  docker build -t musicapp-backend .
# Run:    docker run -p 8000:8000 \
#           -e DATABASE_URL=postgresql://... \
#           -e GOOGLE_CLIENT_ID=... \
#           -e GOOGLE_CLIENT_SECRET=... \
#           -e JWT_SECRET=... \
#           musicapp-backend

FROM python:3.13-slim

# yt-dlp benefits from a JS runtime (nodejs) for newer YouTube formats. Optional
# but recommended — without it some formats are unavailable.
# ffmpeg is required for merging YouTube's separate video + audio streams
# (1080p+ comes as video-only and audio-only that must be combined).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        nodejs \
        ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# App source
COPY app ./app
COPY scripts ./scripts

# Render injects $PORT — default to 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

# --host 0.0.0.0 so the container is reachable; --workers 1 because our
# in-process caches (_user_clients, stream URL cache, OAuth _pending) aren't
# shared across workers. For >10 users, replace these with Redis.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
