# START OF FILE src/api/auth_routes.py
import logging
from fastapi import APIRouter, Response, HTTPException, status, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional

from src.config.settings import settings
from src.core.database_manager import db_manager, User
from src.api.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    get_current_user,
    COOKIE_NAME,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# --- Pydantic Models ---

class AuthInput(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=6, max_length=128)


class AuthResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
    is_main_user: Optional[bool] = None


class AuthStatusResponse(BaseModel):
    authenticated: bool
    username: Optional[str] = None
    is_main_user: Optional[bool] = None
    registration_open: bool = True  # Whether new registrations are allowed


# --- Routes ---

@router.post("/register", response_model=AuthResponse)
async def register(auth_input: AuthInput, response: Response):
    """
    Register a new user account.
    The first registered user automatically becomes the main (admin) user.
    Subsequent registrations require ALLOW_MULTI_USER_REGISTRATION=true in .env.
    """
    user_count = await db_manager.count_users()

    # Check if registration is allowed
    if user_count > 0 and not settings.ALLOW_MULTI_USER_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled. Only the main user account exists. Enable ALLOW_MULTI_USER_REGISTRATION in .env to allow additional accounts.",
        )

    # First user is always the main user
    is_main = user_count == 0

    # Hash password and create user
    hashed = hash_password(auth_input.password)
    user = await db_manager.create_user(
        username=auth_input.username,
        password_hash=hashed,
        is_main_user=is_main,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{auth_input.username}' is already taken.",
        )

    # Auto-login after registration: set JWT cookie
    token = create_access_token(int(user.id), str(user.username))  # type: ignore
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.JWT_EXPIRATION_MINUTES * 60,
        path="/",
    )

    logger.info(f"User '{user.username}' registered successfully (main_user={is_main}).")
    return AuthResponse(
        success=True,
        message=f"Account created successfully. Welcome, {user.username}!",
        username=str(user.username),
        is_main_user=is_main,
    )


@router.post("/login", response_model=AuthResponse)
async def login(auth_input: AuthInput, response: Response):
    """Authenticate with username and password. Sets an HTTPOnly session cookie."""
    user = await db_manager.get_user_by_username(auth_input.username)

    if user is None or not verify_password(auth_input.password, str(user.password_hash)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token = create_access_token(int(user.id), str(user.username))  # type: ignore
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.JWT_EXPIRATION_MINUTES * 60,
        path="/",
    )

    logger.info(f"User '{user.username}' logged in successfully.")
    return AuthResponse(
        success=True,
        message=f"Welcome back, {user.username}!",
        username=str(user.username),
        is_main_user=bool(user.is_main_user),
    )


@router.post("/logout", response_model=AuthResponse)
async def logout(response: Response):
    """Clear the session cookie to log out."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return AuthResponse(success=True, message="Logged out successfully.")


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request):
    """
    Check current authentication status without requiring auth.
    Returns whether the user is authenticated and if registration is open.
    """
    token = request.cookies.get(COOKIE_NAME)
    authenticated = False
    username = None
    is_main_user = None

    if token:
        payload = decode_access_token(token)
        if payload is not None:
            user_id = payload.get("sub")
            if user_id:
                user = await db_manager.get_user_by_id(int(user_id))
                if user:
                    authenticated = True
                    username = str(user.username)
                    is_main_user = bool(user.is_main_user)

    user_count = await db_manager.count_users()
    registration_open = user_count == 0 or settings.ALLOW_MULTI_USER_REGISTRATION

    return AuthStatusResponse(
        authenticated=authenticated,
        username=username,
        is_main_user=is_main_user,
        registration_open=registration_open,
    )


@router.get("/me", response_model=AuthStatusResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """Get the currently authenticated user's info. Requires valid session."""
    user_count = await db_manager.count_users()
    return AuthStatusResponse(
        authenticated=True,
        username=str(current_user.username),
        is_main_user=bool(current_user.is_main_user),
        registration_open=(user_count == 0 or settings.ALLOW_MULTI_USER_REGISTRATION),
    )


@router.get("/check")
async def check_auth_status():
    """
    Lightweight endpoint for the frontend to check if any users exist
    and whether registration is open. Does not require authentication.
    """
    user_count = await db_manager.count_users()
    return {
        "users_exist": user_count > 0,
        "registration_open": user_count == 0 or settings.ALLOW_MULTI_USER_REGISTRATION,
    }
