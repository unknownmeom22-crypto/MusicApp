"""User signup, login, JWT issuing, FastAPI dependency to extract current user.

Token format: standard JWT. Claims: { sub: "<user_id>", email, exp }.
Clients send `Authorization: Bearer <token>` on every request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, EmailStr, Field

from . import db
from .config import settings


# ---------- Models ----------

class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str


class UserOut(BaseModel):
    id: int
    email: str
    has_youtube: bool


# ---------- Hashing ----------

def hash_password(plain: str) -> bytes:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())


def verify_password(plain: str, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), bytes(hashed))
    except Exception:
        return False


# ---------- JWT ----------

def issue_token(user_id: int, email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=settings.jwt_ttl_days)
    payload = {"sub": str(user_id), "email": email, "exp": exp}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
        ) from e


# ---------- DB ops ----------

def create_user(email: str, password: str) -> tuple[int, str]:
    pw_hash = hash_password(password)
    try:
        with db.conn() as c:
            row = c.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email",
                (email.lower(), pw_hash),
            ).fetchone()
    except UniqueViolation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )
    return row["id"], row["email"]


def find_user_by_email(email: str) -> Optional[dict]:
    with db.conn() as c:
        return c.execute(
            "SELECT id, email, password_hash FROM users WHERE email = %s",
            (email.lower(),),
        ).fetchone()


def find_user_by_id(user_id: int) -> Optional[dict]:
    with db.conn() as c:
        return c.execute(
            "SELECT id, email FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()


# ---------- FastAPI dependency ----------

def get_current_user(
    authorization: Annotated[Optional[str], Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Bad token payload")
    user = find_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


def get_optional_user(
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[dict]:
    if not authorization:
        return None
    try:
        return get_current_user(authorization)
    except HTTPException:
        return None
