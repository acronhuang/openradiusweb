"""HTTP routes for the auth feature (Layer 3).

Each handler is a thin shell: parse input → call `service` → serialize output.
Domain exceptions raised by the service layer are translated to HTTP status
codes by the global handler in `gateway.main`.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin
from utils.redis_client import get_redis_client

from . import service
from .schemas import (
    EmailUpdate,
    LoginRequest,
    PasswordChange,
    PasswordReset,
    ROLE_PERMISSIONS,
    TokenResponse,
    UserCreate,
    UserPreferences,
    UserResponse,
    UserUpdate,
)

auth_router = APIRouter(prefix="/auth")
profile_router = APIRouter(prefix="/profile")


# ===========================================================================
# /auth — login, user CRUD, RBAC matrix
# ===========================================================================

@auth_router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user and return JWT token."""
    client_ip = request.client.host if request.client else "unknown"
    r = await get_redis_client()
    token, expires_in = await service.login(
        db, r,
        username=req.username,
        password=req.password,
        client_ip=client_ip,
    )
    return TokenResponse(access_token=token, expires_in=expires_in)


@auth_router.get("/me", response_model=UserResponse)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get current user profile."""
    user = await service.get_user(db, user_id=current_user["sub"])
    return UserResponse(**user)


@auth_router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    req: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Create a new user (admin only)."""
    user = await service.create_user(
        db, current_user,
        username=req.username,
        email=req.email,
        password=req.password,
        role=req.role,
    )
    return UserResponse(**user)


@auth_router.get("/users", response_model=dict)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """List all users with pagination (admin only)."""
    result = await service.list_users(db, page=page, page_size=page_size)
    result["items"] = [UserResponse(**dict(r)) for r in result["items"]]
    return result


@auth_router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Get user detail by ID (admin only)."""
    user = await service.get_user(db, user_id=str(user_id))
    return UserResponse(**user)


@auth_router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    req: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Update user role, email, or enabled status (admin only)."""
    user = await service.update_user(
        db, current_user,
        user_id=str(user_id),
        updates=req.model_dump(),
    )
    return UserResponse(**user)


@auth_router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Delete a user (admin only). Cannot delete yourself."""
    await service.delete_user(db, current_user, user_id=str(user_id))


@auth_router.post("/users/{user_id}/reset-password", status_code=200)
async def reset_user_password(
    user_id: UUID,
    req: PasswordReset,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Admin reset a user's password (admin only)."""
    await service.reset_user_password(
        db, current_user,
        user_id=str(user_id),
        new_password=req.new_password,
    )
    return {"detail": "Password reset successfully"}


@auth_router.get("/roles")
async def get_roles(current_user: dict = Depends(get_current_user)):
    """Return the static role permission matrix."""
    return ROLE_PERMISSIONS


# ===========================================================================
# /profile — self-service
# ===========================================================================

@profile_router.get("", response_model=dict)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get own profile with preferences."""
    user, prefs = await service.get_profile_with_preferences(
        db, user_id=current_user["sub"]
    )
    return {
        "user": UserResponse(**dict(user)),
        "preferences": dict(prefs) if prefs else UserPreferences().model_dump(),
    }


@profile_router.put("/password", status_code=200)
async def change_password(
    req: PasswordChange,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Change own password (requires current password verification)."""
    await service.change_own_password(
        db, current_user,
        current_password=req.current_password,
        new_password=req.new_password,
    )
    return {"detail": "Password changed successfully"}


@profile_router.put("/email", status_code=200)
async def update_email(
    req: EmailUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update own email address."""
    await service.update_own_email(db, current_user, email=req.email)
    return {"detail": "Email updated successfully", "email": req.email}


@profile_router.get("/preferences", response_model=UserPreferences)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get display preferences."""
    prefs = await service.get_own_preferences(db, user_id=current_user["sub"])
    if not prefs:
        return UserPreferences()
    return UserPreferences(**dict(prefs))


@profile_router.put("/preferences", response_model=UserPreferences)
async def update_preferences(
    req: UserPreferences,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update display preferences (timezone, language, theme, notifications)."""
    await service.upsert_own_preferences(
        db, current_user,
        timezone=req.timezone,
        language=req.language,
        theme=req.theme,
        notifications_enabled=req.notifications_enabled,
    )
    return req
