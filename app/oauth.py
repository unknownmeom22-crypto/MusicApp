"""Google OAuth 'device flow' for linking a user's YT Music account.

Flow:
  1. POST /me/yt-oauth/start  → start_device_flow(user)
  2. (user goes to URL on any device, enters user_code, approves)
  3. POST /me/yt-oauth/poll   → poll_device_flow(user)
     → on success, refresh+access tokens are saved into Postgres (yt_auth)
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import HTTPException
from ytmusicapi.auth.oauth import OAuthCredentials

from . import ytm
from .config import settings


_credentials: Optional[OAuthCredentials] = None
_pending: dict[int, dict] = {}


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def get_credentials() -> OAuthCredentials:
    global _credentials
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth is not configured on this server. "
                "Admin must set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            ),
        )
    if _credentials is None:
        _credentials = OAuthCredentials(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
    return _credentials


def start_device_flow(user_id: int) -> dict:
    creds = get_credentials()
    try:
        code = creds.get_code()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Failed to start OAuth: {e}") from e

    if "device_code" not in code or "user_code" not in code:
        raise HTTPException(status_code=502, detail=f"Unexpected OAuth response: {code}")

    _pending[user_id] = {
        "device_code": code["device_code"],
        "expires_at": time.time() + int(code.get("expires_in", 600)),
        "interval": int(code.get("interval", 5)),
    }
    return {
        "verification_url": code.get("verification_url", "https://www.google.com/device"),
        "user_code": code["user_code"],
        "interval": int(code.get("interval", 5)),
        "expires_in": int(code.get("expires_in", 600)),
    }


def poll_device_flow(user_id: int) -> dict:
    state = _pending.get(user_id)
    if not state:
        raise HTTPException(400, "No OAuth session is pending. POST /me/yt-oauth/start first.")
    if time.time() > state["expires_at"]:
        _pending.pop(user_id, None)
        return {"status": "expired"}

    creds = get_credentials()
    try:
        result = creds.token_from_code(state["device_code"])
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "detail": str(e)}

    if isinstance(result, dict) and "access_token" in result:
        save_oauth_token(user_id, result)
        _pending.pop(user_id, None)
        return {"status": "success"}

    err = (result or {}).get("error", "") if isinstance(result, dict) else ""
    if err in ("authorization_pending", "slow_down"):
        return {"status": "pending", "interval": state["interval"]}
    if err == "access_denied":
        _pending.pop(user_id, None)
        return {"status": "denied"}
    if err == "expired_token":
        _pending.pop(user_id, None)
        return {"status": "expired"}

    return {"status": "pending", "interval": state["interval"], "_raw": err}


def save_oauth_token(user_id: int, token_dict: dict) -> None:
    """Persist tokens in ytmusicapi's expected shape, into Postgres."""
    payload = {
        "access_token": token_dict["access_token"],
        "refresh_token": token_dict["refresh_token"],
        "expires_in": int(token_dict.get("expires_in", 3600)),
        "expires_at": int(time.time()) + int(token_dict.get("expires_in", 3600)),
        "scope": token_dict.get("scope", "https://www.googleapis.com/auth/youtube"),
        "token_type": token_dict.get("token_type", "Bearer"),
    }
    ytm.save_user_yt_payload(user_id, payload)


def cancel(user_id: int) -> None:
    _pending.pop(user_id, None)
