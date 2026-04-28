"""LDAP server management routes - CRUD and connection testing."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.ldap_server import LDAPServerCreate, LDAPServerUpdate
from orw_common import nats_client
from middleware.auth import require_operator, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, LDAP_SERVER_UPDATE_COLUMNS

router = APIRouter(prefix="/ldap-servers")


# Columns that should never be returned to the client
_SAFE_COLUMNS = (
    "id, name, description, host, port, use_tls, use_starttls, "
    "bind_dn, base_dn, user_search_filter, user_search_base, "
    "group_search_filter, group_search_base, group_membership_attr, "
    "username_attr, display_name_attr, email_attr, "
    "connect_timeout_seconds, search_timeout_seconds, idle_timeout_seconds, "
    "tls_ca_cert, tls_require_cert, priority, enabled, "
    "last_test_at, last_test_result, last_test_message, tenant_id"
)


# ============================================================
# Endpoints
# ============================================================

@router.get("")
async def list_ldap_servers(
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """List all LDAP servers. Never returns bind_password_encrypted."""
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM ldap_servers WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT {_SAFE_COLUMNS} FROM ldap_servers WHERE {where} "
            f"ORDER BY priority ASC, name ASC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", status_code=201)
async def create_ldap_server(
    req: LDAPServerCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new LDAP server configuration."""
    result = await db.execute(
        text(
            "INSERT INTO ldap_servers "
            "(name, description, host, port, use_tls, use_starttls, "
            "bind_dn, bind_password_encrypted, base_dn, "
            "user_search_filter, user_search_base, "
            "group_search_filter, group_search_base, group_membership_attr, "
            "username_attr, display_name_attr, email_attr, "
            "connect_timeout_seconds, search_timeout_seconds, idle_timeout_seconds, "
            "tls_ca_cert, tls_require_cert, priority, enabled, tenant_id) "
            "VALUES (:name, :description, :host, :port, :use_tls, :use_starttls, "
            ":bind_dn, :bind_password_encrypted, :base_dn, "
            ":user_search_filter, :user_search_base, "
            ":group_search_filter, :group_search_base, :group_membership_attr, "
            ":username_attr, :display_name_attr, :email_attr, "
            ":connect_timeout_seconds, :search_timeout_seconds, :idle_timeout_seconds, "
            ":tls_ca_cert, :tls_require_cert, :priority, :enabled, :tenant_id) "
            f"RETURNING {_SAFE_COLUMNS}"
        ),
        {
            "name": req.name,
            "description": req.description,
            "host": req.host,
            "port": req.port,
            "use_tls": req.use_tls,
            "use_starttls": req.use_starttls,
            "bind_dn": req.bind_dn,
            "bind_password_encrypted": req.bind_password,  # TODO: encrypt via Vault
            "base_dn": req.base_dn,
            "user_search_filter": req.user_search_filter,
            "user_search_base": req.user_search_base,
            "group_search_filter": req.group_search_filter,
            "group_search_base": req.group_search_base,
            "group_membership_attr": req.group_membership_attr,
            "username_attr": req.username_attr,
            "display_name_attr": req.display_name_attr,
            "email_attr": req.email_attr,
            "connect_timeout_seconds": req.connect_timeout_seconds,
            "search_timeout_seconds": req.search_timeout_seconds,
            "idle_timeout_seconds": req.idle_timeout_seconds,
            "tls_ca_cert": req.tls_ca_cert,
            "tls_require_cert": req.tls_require_cert,
            "priority": req.priority,
            "enabled": req.enabled,
            "tenant_id": user["tenant_id"],
        },
    )
    row = result.mappings().first()

    await log_audit(
        db, user, "create", "ldap_server",
        resource_id=str(row["id"]),
        details={"name": req.name, "host": req.host, "port": req.port},
    )

    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "ldap_server_created",
        "ldap_server_id": str(row["id"]),
    })

    return dict(row)


@router.get("/{server_id}")
async def get_ldap_server(
    server_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Get a specific LDAP server. Omits password."""
    result = await db.execute(
        text(
            f"SELECT {_SAFE_COLUMNS} FROM ldap_servers "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="LDAP server not found")
    return dict(row)


@router.put("/{server_id}")
async def update_ldap_server(
    server_id: UUID,
    req: LDAPServerUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update an LDAP server configuration. bind_password is optional."""
    raw = req.model_dump(exclude_unset=True)
    if not raw:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        set_clause, params = build_safe_set_clause(
            raw, LDAP_SERVER_UPDATE_COLUMNS,
            column_map={"bind_password": "bind_password_encrypted"},
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(server_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE ldap_servers SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_SAFE_COLUMNS}"
        ),
        params,
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="LDAP server not found")

    await log_audit(
        db, user, "update", "ldap_server",
        resource_id=str(server_id),
        details={"changed_fields": list(raw.keys())},
    )

    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "ldap_server_updated",
        "ldap_server_id": str(server_id),
    })

    return dict(row)


@router.delete("/{server_id}", status_code=204)
async def delete_ldap_server(
    server_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete an LDAP server. Check not referenced by radius_realms first."""
    # Check for references in radius_realms
    ref_check = await db.execute(
        text(
            "SELECT COUNT(*) FROM radius_realms "
            "WHERE ldap_server_id = :id"
        ),
        {"id": str(server_id)},
    )
    ref_count = ref_check.scalar()
    if ref_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete: LDAP server is referenced by {ref_count} RADIUS realm(s). "
                   f"Remove the realm references first.",
        )

    # Get name for audit before deleting
    name_result = await db.execute(
        text(
            "SELECT name FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": user["tenant_id"]},
    )
    name_row = name_result.mappings().first()
    if not name_row:
        raise HTTPException(status_code=404, detail="LDAP server not found")

    await db.execute(
        text(
            "DELETE FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": user["tenant_id"]},
    )

    await log_audit(
        db, user, "delete", "ldap_server",
        resource_id=str(server_id),
        details={"name": name_row["name"]},
    )

    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "ldap_server_deleted",
        "ldap_server_id": str(server_id),
    })


@router.post("/{server_id}/test")
async def test_ldap_connection(
    server_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """
    Test LDAP connection: connect, bind, and perform a sample search.
    Updates last_test_at/result/message in the database.
    """
    # Load server config including password
    result = await db.execute(
        text(
            "SELECT * FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": user["tenant_id"]},
    )
    server = result.mappings().first()
    if not server:
        raise HTTPException(status_code=404, detail="LDAP server not found")

    # Try to import ldap3
    try:
        import ldap3
        import ssl
    except ImportError:
        await _update_test_result(
            db, server_id, False,
            "ldap3 library is not installed. Install with: pip install ldap3",
        )
        return {
            "success": False,
            "error_message": "ldap3 library is not installed. Install with: pip install ldap3",
        }

    test_result = {
        "success": False,
        "connect_time_ms": None,
        "bind_time_ms": None,
        "search_result_count": None,
        "server_type": None,
        "error_message": None,
    }

    try:
        # Build TLS config if needed
        tls = None
        if server["use_tls"] or server["use_starttls"]:
            tls = ldap3.Tls(
                validate=ssl.CERT_REQUIRED if server["tls_require_cert"] else ssl.CERT_NONE,
            )

        # Determine server URL
        port = server["port"]
        use_ssl = server["use_tls"]

        # Connect
        t0 = time.monotonic()
        ldap_server = ldap3.Server(
            server["host"],
            port=port,
            use_ssl=use_ssl,
            tls=tls,
            get_info=ldap3.ALL,
            connect_timeout=server["connect_timeout_seconds"],
        )
        conn = ldap3.Connection(
            ldap_server,
            user=server["bind_dn"],
            password=server["bind_password_encrypted"],  # TODO: decrypt via Vault
            auto_bind=False,
            raise_exceptions=True,
            receive_timeout=server["search_timeout_seconds"],
        )

        conn.open()
        t1 = time.monotonic()
        test_result["connect_time_ms"] = round((t1 - t0) * 1000, 1)

        # STARTTLS if needed
        if server["use_starttls"]:
            conn.start_tls()

        # Bind
        t2 = time.monotonic()
        conn.bind()
        t3 = time.monotonic()
        test_result["bind_time_ms"] = round((t3 - t2) * 1000, 1)

        # Detect server type from RootDSE
        if ldap_server.info and ldap_server.info.vendor_name:
            test_result["server_type"] = str(ldap_server.info.vendor_name)
        elif ldap_server.info and ldap_server.info.other:
            if "forestFunctionality" in ldap_server.info.other:
                test_result["server_type"] = "Active Directory"
            else:
                test_result["server_type"] = "Generic LDAP"
        else:
            test_result["server_type"] = "Unknown"

        # Sample search
        search_base = server["user_search_base"] or server["base_dn"]
        search_filter = server["user_search_filter"].replace("{0}", "*")
        conn.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=[server["username_attr"]],
            size_limit=10,
        )
        test_result["search_result_count"] = len(conn.entries)
        test_result["success"] = True

        conn.unbind()

    except Exception as e:
        test_result["error_message"] = str(e)

    # Update test results in DB
    test_message = (
        f"OK - connected in {test_result['connect_time_ms']}ms, "
        f"bound in {test_result['bind_time_ms']}ms, "
        f"found {test_result['search_result_count']} entries"
        if test_result["success"]
        else test_result["error_message"]
    )
    await _update_test_result(
        db, server_id, test_result["success"], test_message
    )

    await log_audit(
        db, user, "test", "ldap_server",
        resource_id=str(server_id),
        details={
            "success": test_result["success"],
            "connect_time_ms": test_result["connect_time_ms"],
            "server_type": test_result["server_type"],
        },
    )

    return test_result


# ============================================================
# Helpers
# ============================================================

async def _update_test_result(
    db: AsyncSession,
    server_id: UUID,
    success: bool,
    message: str,
):
    """Update the last test results for an LDAP server."""
    await db.execute(
        text(
            "UPDATE ldap_servers SET "
            "last_test_at = NOW(), "
            "last_test_result = :result, "
            "last_test_message = :message "
            "WHERE id = :id"
        ),
        {
            "id": str(server_id),
            "result": "success" if success else "failure",
            "message": message[:1000],  # Truncate long error messages
        },
    )
