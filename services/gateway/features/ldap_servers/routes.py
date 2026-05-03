"""HTTP routes for the ldap_servers feature (Layer 3).

The `POST /{id}/test` endpoint runs a live LDAP3 connection (open + bind +
sample search). That's pure outbound network I/O against an external
service — kept in routes.py for the same reason settings probes are.
service.py orchestrates the lookup + result-recording, but the actual
ldap3 calls are right here.
"""
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import require_admin, require_operator

from . import service
from .schemas import LDAPServerCreate, LDAPServerUpdate

router = APIRouter(prefix="/ldap-servers")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


# ===========================================================================
# CRUD
# ===========================================================================

@router.get("")
async def list_ldap_servers(
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """List all LDAP servers. Never returns bind_password_encrypted."""
    return await service.list_ldap_servers(
        db, tenant_id=user["tenant_id"],
        enabled=enabled, page=page, page_size=page_size,
    )


@router.post("", status_code=201)
async def create_ldap_server(
    req: LDAPServerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new LDAP server configuration (admin only)."""
    return await service.create_ldap_server(
        db, user,
        fields=req.model_dump(),
        client_ip=_client_ip(request),
    )


@router.get("/{server_id}")
async def get_ldap_server(
    server_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Get a specific LDAP server (without password)."""
    return await service.get_ldap_server(
        db, tenant_id=user["tenant_id"], server_id=server_id,
    )


@router.put("/{server_id}")
async def update_ldap_server(
    server_id: UUID,
    req: LDAPServerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update an LDAP server (admin only). bind_password is optional."""
    return await service.update_ldap_server(
        db, user,
        server_id=server_id,
        updates=req.model_dump(exclude_unset=True),
        client_ip=_client_ip(request),
    )


@router.delete("/{server_id}", status_code=204)
async def delete_ldap_server(
    server_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete an LDAP server (admin only). Refuses if referenced by realms."""
    await service.delete_ldap_server(
        db, user,
        server_id=server_id,
        client_ip=_client_ip(request),
    )


# ===========================================================================
# Live LDAP connection test
# ===========================================================================

@router.post("/{server_id}/test")
async def test_ldap_connection(
    server_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Test LDAP connection: open + bind + sample search. Records outcome."""
    server = await service.lookup_for_test(
        db, tenant_id=user["tenant_id"], server_id=server_id,
    )

    try:
        import ldap3
        import ssl
    except ImportError:
        msg = "ldap3 library is not installed. Install with: pip install ldap3"
        await service.record_test_result(
            db, user, server_id=server_id, success=False, message=msg,
            audit_details={"success": False, "error": "ldap3 missing"},
        )
        return {"success": False, "error_message": msg}

    test_result: dict[str, Any] = {
        "success": False,
        "connect_time_ms": None,
        "bind_time_ms": None,
        "search_result_count": None,
        "server_type": None,
        "error_message": None,
    }

    try:
        tls = None
        if server["use_tls"] or server["use_starttls"]:
            # tls_require_cert is a VARCHAR enum: never|allow|try|demand
            require_strict = server["tls_require_cert"] in ("demand", "try")
            tls = ldap3.Tls(
                validate=ssl.CERT_REQUIRED if require_strict else ssl.CERT_NONE,
            )

        t0 = time.monotonic()
        ldap_server = ldap3.Server(
            server["host"],
            port=server["port"],
            use_ssl=server["use_tls"],
            tls=tls,
            get_info=ldap3.ALL,
            connect_timeout=server["connect_timeout_seconds"],
        )
        conn = ldap3.Connection(
            ldap_server,
            user=server["bind_dn"],
            password=server["bind_password_encrypted"],  # already decrypted by lookup_full_for_test
            auto_bind=False,
            raise_exceptions=True,
            receive_timeout=server["search_timeout_seconds"],
        )

        conn.open()
        test_result["connect_time_ms"] = round((time.monotonic() - t0) * 1000, 1)

        if server["use_starttls"]:
            conn.start_tls()

        t2 = time.monotonic()
        conn.bind()
        test_result["bind_time_ms"] = round((time.monotonic() - t2) * 1000, 1)

        if ldap_server.info and ldap_server.info.vendor_name:
            test_result["server_type"] = str(ldap_server.info.vendor_name)
        elif ldap_server.info and ldap_server.info.other:
            if "forestFunctionality" in ldap_server.info.other:
                test_result["server_type"] = "Active Directory"
            else:
                test_result["server_type"] = "Generic LDAP"
        else:
            test_result["server_type"] = "Unknown"

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

    test_message = (
        f"OK - connected in {test_result['connect_time_ms']}ms, "
        f"bound in {test_result['bind_time_ms']}ms, "
        f"found {test_result['search_result_count']} entries"
        if test_result["success"]
        else test_result["error_message"]
    )
    await service.record_test_result(
        db, user,
        server_id=server_id,
        success=test_result["success"],
        message=test_message,
        audit_details={
            "success": test_result["success"],
            "connect_time_ms": test_result["connect_time_ms"],
            "server_type": test_result["server_type"],
        },
    )
    return test_result
