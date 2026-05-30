# MusicApp backend — production Dockerfile
#
# Based on the bgutil PO Token provider image so its Node runtime + provider
# server (with all native libs) are present and working natively. We add Python
# on top; the app launches the provider on startup (see app/main.py) so yt-dlp's
# bgutil plugin can attach PO tokens — which restores YouTube formats that get
# withheld from datacenter IPs (Render).
#
# Build:  docker build -t musicapp-backend .
# Run:    docker run -p 8000:8000 -e DATABASE_URL=... -e JWT_SECRET=... musicapp-backend

FROM brainicism/bgutil-ytdlp-pot-provider:node

# Add Python (for the API) + ffmpeg (stream merging). The provider already
# ships Node + its server at /app.
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Isolated venv — Debian's system pip is externally-managed (PEP 668).
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /srv

# Python deps first for layer caching (includes the bgutil pip plugin).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Override the provider image's ENTRYPOINT (node build/main.js) — we run uvicorn,
# and the app spawns the provider itself on startup.
ENTRYPOINT []

# Where the provider server lives in this base image (app/main.py reads this).
ENV PORT=8000 \
    BGUTIL_PROVIDER_CWD=/app
EXPOSE 8000

# --workers 1 because the in-process stream-URL cache isn't shared, and so the
# app launches a single provider subprocess.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
