"""Profile routes - self-service profile, password, email, preferences."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.auth import (
    EmailUpdate, PasswordChange, UserPreferences, UserResponse,
)
from middleware.auth import (
    get_current_user, hash_password, verify_password,
)
from utils.audit import log_audit

router = APIRouter(prefix="/profile")


@router.get("", response_model=dict)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get own profile with preferences."""
    result = await db.execute(
        text(
            "SELECT id, username, email, role, enabled, last_login, created_at "
            "FROM users WHERE id = :id"
        ),
        {"id": current_user["sub"]},
    )
    user = result.mappings().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch preferences
    pref_result = await db.execute(
        text(
            "SELECT timezone, language, theme, notifications_enabled "
            "FROM user_preferences WHERE user_id = :user_id"
        ),
        {"user_id": current_user["sub"]},
    )
    prefs = pref_result.mappings().first()

    return {
        "user": UserResponse(**dict(user)),
        "preferences": dict(prefs) if prefs else UserPreferences().model_dump(),
    }


@router.put("/password", status_code=200)
async def change_password(
    req: PasswordChange,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Change own password (requires current password verification)."""
    # Fetch current hash
    result = await db.execute(
        text("SELECT password_hash FROM users WHERE id = :id"),
        {"id": current_user["sub"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(req.current_password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    await db.execute(
        text(
            "UPDATE users SET password_hash = :password_hash, updated_at = NOW() "
            "WHERE id = :id"
        ),
        {
            "id": current_user["sub"],
            "password_hash": hash_password(req.new_password),
        },
    )

    await log_audit(
        db, current_user,
        action="change_password",
        resource_type="user",
        resource_id=current_user["sub"],
    )

    return {"detail": "Password changed successfully"}


@router.put("/email", status_code=200)
async def update_email(
    req: EmailUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update own email address."""
    email = req.email

    await db.execute(
        text(
            "UPDATE users SET email = :email, updated_at = NOW() "
            "WHERE id = :id"
        ),
        {"id": current_user["sub"], "email": email},
    )

    await log_audit(
        db, current_user,
        action="update_email",
        resource_type="user",
        resource_id=current_user["sub"],
        details={"new_email": email},
    )

    return {"detail": "Email updated successfully", "email": email}


@router.get("/preferences", response_model=UserPreferences)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get display preferences."""
    result = await db.execute(
        text(
            "SELECT timezone, language, theme, notifications_enabled "
            "FROM user_preferences WHERE user_id = :user_id"
        ),
        {"user_id": current_user["sub"]},
    )
    row = result.mappings().first()
    if not row:
        return UserPreferences()
    return UserPreferences(**dict(row))


@router.put("/preferences", response_model=UserPreferences)
async def update_preferences(
    req: UserPreferences,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update display preferences (timezone, language, theme, notifications)."""
    await db.execute(
        text(
            "INSERT INTO user_preferences (user_id, timezone, language, theme, notifications_enabled) "
            "VALUES (:user_id::uuid, :timezone, :language, :theme, :notifications_enabled) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "timezone = :timezone, language = :language, "
            "theme = :theme, notifications_enabled = :notifications_enabled, "
            "updated_at = NOW()"
        ),
        {
            "user_id": current_user["sub"],
            "timezone": req.timezone,
            "language": req.language,
            "theme": req.theme,
            "notifications_enabled": req.notifications_enabled,
        },
    )
    return req
