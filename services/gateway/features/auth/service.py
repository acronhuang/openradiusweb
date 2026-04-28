"""Use-case composition for the auth feature (Layer 2).

Each function orchestrates atoms from `repository.py`, the rate-limit /
lockout counters in Redis, password hashing, and audit logging. None of
these functions know they are running inside FastAPI; they raise domain
exceptions (see `orw_common.exceptions`) which `gateway.main` translates
to HTTP status codes.
"""
from typing import Any, Mapping, Optional

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from middleware.auth import (
    create_access_token,
    hash_password,
    verify_password,
)
from utils.audit import log_audit

from . import repository as repo


# ---------------------------------------------------------------------------
# Rate limiting / lockout constants
# ---------------------------------------------------------------------------
IP_RATE_LIMIT = 20            # max login attempts per IP per minute
IP_RATE_TTL = 60              # seconds
FAIL_LOCKOUT_THRESHOLD = 5    # lock after N failed attempts
FAIL_LOCKOUT_TTL = 900        # 15 minutes


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

async def _check_ip_rate(r: redis.Redis, client_ip: str) -> None:
    key = f"orw:login_rate:{client_ip}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, IP_RATE_TTL)
    if count > IP_RATE_LIMIT:
        raise RateLimitError("Too many login attempts. Try again later.")


async def _check_lockout(r: redis.Redis, username: str) -> None:
    if await r.exists(f"orw:lockout:{username}"):
        raise RateLimitError(
            "Account temporarily locked due to repeated failed login attempts."
        )


async def _record_failed_login(r: redis.Redis, username: str) -> int:
    """Increment the failure counter and arm the lockout if threshold reached. Returns new count."""
    fail_key = f"orw:login_fail:{username}"
    count = await r.incr(fail_key)
    if count == 1:
        await r.expire(fail_key, FAIL_LOCKOUT_TTL)
    if count >= FAIL_LOCKOUT_THRESHOLD:
        await r.setex(f"orw:lockout:{username}", FAIL_LOCKOUT_TTL, "locked")
    return count


async def _clear_login_failures(r: redis.Redis, username: str) -> None:
    await r.delete(f"orw:login_fail:{username}", f"orw:lockout:{username}")


async def login(
    db: AsyncSession,
    r: redis.Redis,
    *,
    username: str,
    password: str,
    client_ip: str,
) -> tuple[str, int]:
    """Authenticate and return (jwt, expires_in_seconds).

    Raises:
        RateLimitError: IP-rate or account-lockout exceeded.
        AuthenticationError: Bad credentials, disabled, or unknown user.
    """
    await _check_ip_rate(r, client_ip)
    await _check_lockout(r, username)

    user = await repo.lookup_user_for_login(db, username)
    if not user or not user["enabled"] or not verify_password(password, user["password_hash"]):
        attempt = await _record_failed_login(r, username)
        await log_audit(
            db, {"sub": None, "tenant_id": None},
            action="login_failed", resource_type="auth",
            details={"username": username, "attempt": attempt},
            ip_address=client_ip,
        )
        # Commit the audit row before raising — caller's session may rollback.
        await db.commit()
        raise AuthenticationError("Invalid credentials")

    await _clear_login_failures(r, username)
    await repo.update_last_login(db, str(user["id"]))
    await log_audit(
        db, {"sub": str(user["id"]), "tenant_id": str(user["tenant_id"])},
        action="login_success", resource_type="auth",
        details={"username": user["username"]},
        ip_address=client_ip,
    )
    return create_access_token(
        user_id=str(user["id"]),
        username=user["username"],
        role=user["role"],
        tenant_id=str(user["tenant_id"]),
    )


# ---------------------------------------------------------------------------
# User management (admin)
# ---------------------------------------------------------------------------

async def create_user(
    db: AsyncSession,
    actor: dict,
    *,
    username: str,
    email: Optional[str],
    password: str,
    role: str,
) -> Mapping[str, Any]:
    if await repo.username_exists(db, username):
        raise ConflictError("Username already exists")

    user = await repo.insert_user(
        db,
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="user",
        resource_id=str(user["id"]),
        details={"username": username, "role": role},
    )
    return user


async def update_user(
    db: AsyncSession,
    actor: dict,
    *,
    user_id: str,
    updates: dict,
) -> Mapping[str, Any]:
    cleaned = {k: v for k, v in updates.items() if v is not None}
    if not cleaned:
        raise ValidationError("No fields to update")

    try:
        user = await repo.update_user_fields(db, user_id, cleaned)
    except ValueError:
        raise ValidationError("No valid fields to update")

    if user is None:
        raise NotFoundError("user", user_id)

    await log_audit(
        db, actor,
        action="update", resource_type="user",
        resource_id=user_id,
        details={"changed_fields": list(cleaned.keys())},
    )
    return user


async def delete_user(db: AsyncSession, actor: dict, *, user_id: str) -> None:
    if user_id == actor.get("sub"):
        raise ValidationError("Cannot delete your own account")

    if not await repo.delete_user(db, user_id):
        raise NotFoundError("user", user_id)

    await log_audit(
        db, actor,
        action="delete", resource_type="user",
        resource_id=user_id,
    )


async def reset_user_password(
    db: AsyncSession,
    actor: dict,
    *,
    user_id: str,
    new_password: str,
) -> None:
    if not await repo.update_password_hash(db, user_id, hash_password(new_password)):
        raise NotFoundError("user", user_id)
    await log_audit(
        db, actor,
        action="reset_password", resource_type="user",
        resource_id=user_id,
    )


async def get_user(db: AsyncSession, *, user_id: str) -> Mapping[str, Any]:
    user = await repo.lookup_user_profile(db, user_id)
    if not user:
        raise NotFoundError("user", user_id)
    return user


async def list_users(db: AsyncSession, *, page: int, page_size: int) -> dict:
    total = await repo.count_users(db)
    rows = await repo.list_users_paginated(
        db, limit=page_size, offset=(page - 1) * page_size
    )
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Self-service (profile)
# ---------------------------------------------------------------------------

async def get_profile_with_preferences(
    db: AsyncSession, *, user_id: str
) -> tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]:
    """Fetch (user_profile, preferences_or_None). Raises NotFoundError if no user."""
    user = await repo.lookup_user_profile(db, user_id)
    if not user:
        raise NotFoundError("user", user_id)
    prefs = await repo.lookup_user_preferences(db, user_id)
    return user, prefs


async def change_own_password(
    db: AsyncSession,
    actor: dict,
    *,
    current_password: str,
    new_password: str,
) -> None:
    user_id = actor["sub"]
    current_hash = await repo.lookup_password_hash(db, user_id)
    if current_hash is None:
        raise NotFoundError("user", user_id)
    if not verify_password(current_password, current_hash):
        raise ValidationError("Current password is incorrect")

    await repo.update_password_hash(db, user_id, hash_password(new_password))
    await log_audit(
        db, actor,
        action="change_password", resource_type="user",
        resource_id=user_id,
    )


async def update_own_email(db: AsyncSession, actor: dict, *, email: str) -> None:
    user_id = actor["sub"]
    await repo.update_email(db, user_id, email)
    await log_audit(
        db, actor,
        action="update_email", resource_type="user",
        resource_id=user_id,
        details={"new_email": email},
    )


async def get_own_preferences(
    db: AsyncSession, *, user_id: str
) -> Optional[Mapping[str, Any]]:
    """Returns the preferences row, or None if the user has none yet."""
    return await repo.lookup_user_preferences(db, user_id)


async def upsert_own_preferences(
    db: AsyncSession,
    actor: dict,
    *,
    timezone: str,
    language: str,
    theme: str,
    notifications_enabled: bool,
) -> None:
    await repo.upsert_user_preferences(
        db,
        user_id=actor["sub"],
        timezone=timezone,
        language=language,
        theme=theme,
        notifications_enabled=notifications_enabled,
    )
