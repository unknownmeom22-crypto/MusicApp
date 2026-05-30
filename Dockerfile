# MusicApp backend — production Dockerfile
#
# Bundles the bgutil PO Token provider
# (https://github.com/Brainicism/bgutil-ytdlp-pot-provider) so yt-dlp can satisfy
# YouTube's "Sign in to confirm you're not a bot" check on datacenter IPs
# (Render) WITHOUT cookies. The provider runs as a localhost HTTP server
# (127.0.0.1:4416) alongside uvicorn; the bgutil-ytdlp-pot-provider pip plugin
# auto-discovers it and attaches tokens to yt-dlp requests.
#
# Build:  docker build -t musicapp-backend .
# Run:    docker run -p 8000:8000 -e DATABASE_URL=... -e JWT_SECRET=... musicapp-backend

# --- Prebuilt POT provider. Its base is node:25-bookworm-slim (Debian/glibc),
#     ABI-compatible with the python:slim base below, so we can copy its node
#     binary + built server (incl. native modules) and run them as-is. ---
FROM brainicism/bgutil-ytdlp-pot-provider:node AS potprovider

# Pin bookworm so the Debian release (and glibc) matches the node image above.
FROM python:3.13-slim-bookworm

# ffmpeg merges YouTube's separate video+audio streams (1080p+); ca-certificates
# for TLS. Node is copied from the provider image (below) rather than apt-
# installed, so it matches the provider's build and also serves yt-dlp's JS needs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Node runtime + the built POT provider server from the official image.
COPY --from=potprovider /usr/local/bin/node /usr/local/bin/node
COPY --from=potprovider /app /opt/bgutil-provider

WORKDIR /app

# Python deps first for layer caching (includes the bgutil pip plugin).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# App source
COPY app ./app
COPY scripts ./scripts
COPY start.sh ./start.sh
RUN chmod +x ./start.sh

# Render injects $PORT — default to 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

# start.sh launches the POT provider in the background, then exec's uvicorn as
# PID 1. (--workers 1 because the in-process stream-URL cache isn't shared.)
CMD ["./start.sh"]
