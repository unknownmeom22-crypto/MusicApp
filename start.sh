#!/bin/sh
# Container entrypoint: run the bgutil PO Token provider (localhost:4416) in the
# background so yt-dlp's bgutil plugin can fetch tokens, then run the API.
#
# The provider failing is non-fatal: yt-dlp simply proceeds without a po_token
# (which only matters on bot-walled datacenter IPs), so the API still starts.
set -e

(
  cd /opt/bgutil-provider && exec node build/main.js
) >/tmp/pot-provider.log 2>&1 &
echo "[start] POT provider launched (pid $!) on 127.0.0.1:4416"

# Give the provider a moment to boot its BotGuard VM before the first request.
sleep 3

echo "[start] launching uvicorn on 0.0.0.0:${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
