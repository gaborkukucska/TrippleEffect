# START OF FILE src/api/auth.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import bcrypt


from fastapi import Request, HTTPException, status, WebSocket

from src.config.settings import settings
from src.core.database_manager import db_manager, User

logger = logging.getLogger(__name__)

# --- Password Hashing ---

def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except ValueError:
        return False


# --- JWT Token Management ---
COOKIE_NAME = "trippleeffect_session"


def create_access_token(user_id: int, username: str) -> str:
    """Create a JWT access token with user_id and username claims."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token. Returns the payload dict or None."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token has expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid JWT token: {e}")
        return None


# --- FastAPI Dependencies ---

async def get_current_user(request: Request) -> User:
    """
    FastAPI dependency that extracts and validates the JWT session cookie.
    Returns the authenticated User object or raises 401.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload.",
        )

    user = await db_manager.get_user_by_id(int(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found. Account may have been deleted.",
        )

    return user


async def get_current_user_ws(websocket: WebSocket) -> Optional[User]:
    """
    WebSocket-specific auth check. Reads the JWT from the cookie header
    during the WebSocket handshake. Returns User or None (does not raise).
    """
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        return None

    payload = decode_access_token(token)
    if payload is None:
        return None

    user_id = payload.get("sub")
    if user_id is None:
        return None

    user = await db_manager.get_user_by_id(int(user_id))
    return user
