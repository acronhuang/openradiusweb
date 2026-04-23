"""Authentication routes - login, user management."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.auth import (
    LoginRequest, TokenResponse, UserCreate, UserResponse,
    UserUpdate, PasswordReset,
)
from middleware.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin,
)
from utils.audit import log_audit
from utils.redis_client import get_redis_client
from utils.safe_sql import build_safe_set_clause, USER_UPDATE_COLUMNS

router = APIRouter(prefix="/auth")

# Rate limiting / lockout constants
_IP_RATE_LIMIT = 20          # max login attempts per IP per minute
_IP_RATE_TTL = 60            # seconds
_FAIL_LOCKOUT_THRESHOLD = 5  # lock after N failed attempts
_FAIL_LOCKOUT_TTL = 900      # 15 minutes


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user and return JWT token."""
    client_ip = request.client.host if request.client else "unknown"
    r = await get_redis_client()

    # --- IP-based rate limiting ---
    ip_key = f"orw:login_rate:{client_ip}"
    ip_count = await r.incr(ip_key)
    if ip_count == 1:
        await r.expire(ip_key, _IP_RATE_TTL)
    if ip_count > _IP_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )

    # --- Account lockout check ---
    lockout_key = f"orw:lockout:{req.username}"
    if await r.exists(lockout_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to repeated failed login attempts.",
        )

    # --- Lookup user ---
    result = await db.execute(
        text(
            "SELECT id, username, password_hash, role, tenant_id, enabled "
            "FROM users WHERE username = :username"
        ),
        {"username": req.username},
    )
    user = result.mappings().first()

    if not user or not user["enabled"] or not verify_password(req.password, user["password_hash"]):
        # Track failed attempt
        fail_key = f"orw:login_fail:{req.username}"
        fail_count = await r.incr(fail_key)
        if fail_count == 1:
            await r.expire(fail_key, _FAIL_LOCKOUT_TTL)
        if fail_count >= _FAIL_LOCKOUT_THRESHOLD:
            await r.setex(lockout_key, _FAIL_LOCKOUT_TTL, "locked")

        # Audit: login failure (commit before raising so rollback doesn't lose it)
        await log_audit(
            db, {"sub": None, "tenant_id": None},
            action="login_failed", resource_type="auth",
            details={"username": req.username, "attempt": fail_count},
            ip_address=client_ip,
        )
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # --- Login success ---
    # Clear fail counters
    await r.delete(f"orw:login_fail:{req.username}", lockout_key)

    # Update last_login
    await db.execute(
        text("UPDATE users SET last_login = NOW() WHERE id = :id"),
        {"id": str(user["id"])},
    )

    # Audit: login success
    await log_audit(
        db, {"sub": str(user["id"]), "tenant_id": str(user["tenant_id"])},
        action="login_success", resource_type="auth",
        details={"username": user["username"]},
        ip_address=client_ip,
    )

    token, expires_in = create_access_token(
        user_id=str(user["id"]),
        username=user["username"],
        role=user["role"],
        tenant_id=str(user["tenant_id"]),
    )
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    req: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Create a new user (admin only)."""
    # Check if username exists
    exists = await db.execute(
        text("SELECT 1 FROM users WHERE username = :username"),
        {"username": req.username},
    )
    if exists.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    result = await db.execute(
        text(
            "INSERT INTO users (username, email, password_hash, role, tenant_id) "
            "VALUES (:username, :email, :password_hash, :role, "
            "(SELECT id FROM tenants WHERE name = 'default')) "
            "RETURNING id, username, email, role, enabled, last_login, created_at"
        ),
        {
            "username": req.username,
            "email": req.email,
            "password_hash": hash_password(req.password),
            "role": req.role,
        },
    )
    user = result.mappings().first()

    await log_audit(
        db, current_user,
        action="create", resource_type="user",
        resource_id=str(user["id"]),
        details={"username": req.username, "role": req.role},
    )

    return UserResponse(**user)


@router.get("/me", response_model=UserResponse)
async def get_me(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get current user profile."""
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
    return UserResponse(**user)


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=dict)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """List all users with pagination (admin only)."""
    # Count total
    count_result = await db.execute(text("SELECT COUNT(*) FROM users"))
    total = count_result.scalar()

    offset = (page - 1) * page_size
    result = await db.execute(
        text(
            "SELECT id, username, email, role, enabled, last_login, created_at "
            "FROM users ORDER BY created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {"limit": page_size, "offset": offset},
    )
    rows = result.mappings().all()

    return {
        "items": [UserResponse(**dict(r)) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Get user detail by ID (admin only)."""
    result = await db.execute(
        text(
            "SELECT id, username, email, role, enabled, last_login, created_at "
            "FROM users WHERE id = :id"
        ),
        {"id": str(user_id)},
    )
    user = result.mappings().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(**user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    req: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Update user role, email, or enabled status (admin only)."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        set_clause, params = build_safe_set_clause(updates, USER_UPDATE_COLUMNS)
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(user_id)

    result = await db.execute(
        text(
            f"UPDATE users SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id "
            f"RETURNING id, username, email, role, enabled, last_login, created_at"
        ),
        params,
    )
    user = result.mappings().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await log_audit(
        db, current_user,
        action="update",
        resource_type="user",
        resource_id=str(user_id),
        details={"changed_fields": list(req.model_dump(exclude_none=True).keys())},
    )

    return UserResponse(**user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Delete a user (admin only). Cannot delete yourself."""
    if str(user_id) == current_user["sub"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    result = await db.execute(
        text("DELETE FROM users WHERE id = :id RETURNING id"),
        {"id": str(user_id)},
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="User not found")

    await log_audit(
        db, current_user,
        action="delete",
        resource_type="user",
        resource_id=str(user_id),
    )


@router.post("/users/{user_id}/reset-password", status_code=200)
async def reset_user_password(
    user_id: UUID,
    req: PasswordReset,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Admin reset a user's password (admin only)."""
    result = await db.execute(
        text(
            "UPDATE users SET password_hash = :password_hash, updated_at = NOW() "
            "WHERE id = :id RETURNING id"
        ),
        {
            "id": str(user_id),
            "password_hash": hash_password(req.new_password),
        },
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="User not found")

    await log_audit(
        db, current_user,
        action="reset_password",
        resource_type="user",
        resource_id=str(user_id),
    )

    return {"detail": "Password reset successfully"}


# ---------------------------------------------------------------------------
# Role permission matrix (any authenticated user)
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS = {
    "admin": {
        "description": "Full system access",
        "permissions": [
            "users.read", "users.create", "users.update", "users.delete",
            "devices.read", "devices.create", "devices.update", "devices.delete",
            "policies.read", "policies.create", "policies.update", "policies.delete",
            "network_devices.read", "network_devices.create",
            "network_devices.update", "network_devices.delete",
            "radius.read", "radius.update",
            "audit.read",
            "coa.send",
        ],
    },
    "operator": {
        "description": "Operational access - manage devices and policies",
        "permissions": [
            "devices.read", "devices.create", "devices.update",
            "policies.read", "policies.create", "policies.update",
            "network_devices.read", "network_devices.create",
            "network_devices.update",
            "radius.read",
            "audit.read",
            "coa.send",
        ],
    },
    "viewer": {
        "description": "Read-only access",
        "permissions": [
            "devices.read",
            "policies.read",
            "network_devices.read",
            "radius.read",
            "audit.read",
        ],
    },
}


@router.get("/roles")
async def get_roles(
    current_user: dict = Depends(get_current_user),
):
    """Return the static role permission matrix."""
    return ROLE_PERMISSIONS
