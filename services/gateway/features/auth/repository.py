"""Database atoms for the auth feature.

Each function performs a single DB operation (Resolver / Query / Repository
per development-manual.md §3.1). No business logic, no exceptions raised
beyond what asyncpg/sqlalchemy normally raises.
"""
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, USER_UPDATE_COLUMNS


# ---------------------------------------------------------------------------
# User reads
# ---------------------------------------------------------------------------

async def lookup_user_for_login(db: AsyncSession, username: str) -> Optional[Mapping[str, Any]]:
    """Fetch the columns needed to authenticate a login attempt."""
    result = await db.execute(
        text(
            "SELECT id, username, password_hash, role, tenant_id, enabled "
            "FROM users WHERE username = :username"
        ),
        {"username": username},
    )
    return result.mappings().first()


async def lookup_user_profile(db: AsyncSession, user_id: str) -> Optional[Mapping[str, Any]]:
    """Fetch the profile-shape columns for a user by ID."""
    result = await db.execute(
        text(
            "SELECT id, username, email, role, enabled, last_login, created_at "
            "FROM users WHERE id = :id"
        ),
        {"id": user_id},
    )
    return result.mappings().first()


async def lookup_password_hash(db: AsyncSession, user_id: str) -> Optional[str]:
    """Fetch only the password hash (used by self-service password change)."""
    result = await db.execute(
        text("SELECT password_hash FROM users WHERE id = :id"),
        {"id": user_id},
    )
    row = result.mappings().first()
    return row["password_hash"] if row else None


async def username_exists(db: AsyncSession, username: str) -> bool:
    result = await db.execute(
        text("SELECT 1 FROM users WHERE username = :username"),
        {"username": username},
    )
    return result.first() is not None


async def count_users(db: AsyncSession) -> int:
    result = await db.execute(text("SELECT COUNT(*) FROM users"))
    return int(result.scalar() or 0)


async def list_users_paginated(
    db: AsyncSession, *, limit: int, offset: int
) -> list[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT id, username, email, role, enabled, last_login, created_at "
            "FROM users ORDER BY created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {"limit": limit, "offset": offset},
    )
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# User writes
# ---------------------------------------------------------------------------

async def insert_user(
    db: AsyncSession,
    *,
    username: str,
    email: Optional[str],
    password_hash: str,
    role: str,
) -> Mapping[str, Any]:
    """Insert a user into the default tenant. Returns the new row's profile shape."""
    result = await db.execute(
        text(
            "INSERT INTO users (username, email, password_hash, role, tenant_id) "
            "VALUES (:username, :email, :password_hash, :role, "
            "(SELECT id FROM tenants WHERE name = 'default')) "
            "RETURNING id, username, email, role, enabled, last_login, created_at"
        ),
        {
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "role": role,
        },
    )
    row = result.mappings().first()
    if row is None:
        # RETURNING on INSERT cannot return None if the INSERT succeeded; defensive.
        raise RuntimeError("INSERT users RETURNING produced no row")
    return row


async def update_user_fields(
    db: AsyncSession, user_id: str, updates: dict
) -> Optional[Mapping[str, Any]]:
    """Apply a partial update using the safe SET clause builder.

    Returns the updated row (profile shape), or None if no row matched.
    Raises ValueError if `updates` contains no allowed columns.
    """
    set_clause, params = build_safe_set_clause(updates, USER_UPDATE_COLUMNS)
    params["id"] = user_id
    result = await db.execute(
        text(
            f"UPDATE users SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id "
            f"RETURNING id, username, email, role, enabled, last_login, created_at"
        ),
        params,
    )
    return result.mappings().first()


async def update_password_hash(db: AsyncSession, user_id: str, password_hash: str) -> bool:
    """Returns True if a row was updated, False if no user matched."""
    result = await db.execute(
        text(
            "UPDATE users SET password_hash = :password_hash, updated_at = NOW() "
            "WHERE id = :id RETURNING id"
        ),
        {"id": user_id, "password_hash": password_hash},
    )
    return result.first() is not None


async def update_email(db: AsyncSession, user_id: str, email: str) -> None:
    await db.execute(
        text(
            "UPDATE users SET email = :email, updated_at = NOW() "
            "WHERE id = :id"
        ),
        {"id": user_id, "email": email},
    )


async def update_last_login(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        text("UPDATE users SET last_login = NOW() WHERE id = :id"),
        {"id": user_id},
    )


async def delete_user(db: AsyncSession, user_id: str) -> bool:
    """Returns True if a row was deleted, False if no user matched."""
    result = await db.execute(
        text("DELETE FROM users WHERE id = :id RETURNING id"),
        {"id": user_id},
    )
    return result.first() is not None


# ---------------------------------------------------------------------------
# User preferences
# ---------------------------------------------------------------------------

async def lookup_user_preferences(
    db: AsyncSession, user_id: str
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT timezone, language, theme, notifications_enabled "
            "FROM user_preferences WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    )
    return result.mappings().first()


async def upsert_user_preferences(
    db: AsyncSession,
    *,
    user_id: str,
    timezone: str,
    language: str,
    theme: str,
    notifications_enabled: bool,
) -> None:
    await db.execute(
        text(
            "INSERT INTO user_preferences "
            "(user_id, timezone, language, theme, notifications_enabled) "
            "VALUES (:user_id::uuid, :timezone, :language, :theme, :notifications_enabled) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "timezone = :timezone, language = :language, "
            "theme = :theme, notifications_enabled = :notifications_enabled, "
            "updated_at = NOW()"
        ),
        {
            "user_id": user_id,
            "timezone": timezone,
            "language": language,
            "theme": theme,
            "notifications_enabled": notifications_enabled,
        },
    )
