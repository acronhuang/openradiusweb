"""CRUD smoke tests against a real Postgres.

Each test calls the actual repository functions (the same code paths
that run in prod) against a real DB. The assertions are minimal —
this layer is about catching SQL that compiles but blows up at
execution time:

  - PR #32: `:name::type` casts that asyncpg's named-parameter
    preprocessor mangles into "syntax error at or near :"
  - asyncpg type-coercion mismatches (e.g. PR #40, where bool was
    bound to a VARCHAR column) — partially covered by the contract
    test, but real execution catches the long tail
  - column references that don't exist (typos, renamed columns)
  - missing required parameters in the params dict

We're NOT trying to test business rules here. That's what the
existing unit tests (with mocks) already do well.

Coverage strategy: one Insert+List+Lookup+Update+Delete test per
high-traffic feature. Add new features incrementally as we touch
their repositories.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from features.ldap_servers import repository as ldap_repo
from features.nas_clients import repository as nas_repo
from features.vlans import repository as vlan_repo


# ---------------------------------------------------------------------------
# nas_clients — the feature that surfaced PR #32
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nas_client_insert_list_update_delete(db_session, tenant_id):
    inserted = await nas_repo.insert_nas_client(
        db_session,
        tenant_id=tenant_id,
        name="test-switch-01",
        ip_address="10.0.0.1",
        shared_secret="encrypted-blob-here",
        shortname=None,
        nas_type="cisco",
        description="Smoke-test fixture",
    )
    assert inserted["name"] == "test-switch-01"
    assert inserted["ip_address"] == "10.0.0.1"
    nas_id = UUID(str(inserted["id"]))

    # List should return the row we just inserted.
    rows = await nas_repo.list_nas_clients(db_session, tenant_id=tenant_id)
    assert any(r["id"] == inserted["id"] for r in rows)

    # Update only some fields — exercises build_safe_set_clause.
    updated = await nas_repo.update_nas_client(
        db_session,
        tenant_id=tenant_id,
        nas_id=nas_id,
        updates={"description": "edited", "nas_type": "juniper"},
    )
    assert updated is not None
    assert updated["description"] == "edited"
    assert updated["nas_type"] == "juniper"

    # Delete and confirm the lookup turns up empty.
    await nas_repo.delete_nas_client(
        db_session, tenant_id=tenant_id, nas_id=nas_id,
    )
    gone = await nas_repo.lookup_nas_client(
        db_session, tenant_id=tenant_id, nas_id=nas_id,
    )
    assert gone is None


# ---------------------------------------------------------------------------
# ldap_servers — 25-column INSERT, exercises bind_password column rename
# ---------------------------------------------------------------------------

_LDAP_BASE_FIELDS = {
    "name": "test-ldap",
    "description": "smoke",
    "host": "ldap.example.com",
    "port": 389,
    "use_tls": False,
    "use_starttls": False,
    "bind_dn": "CN=svc,OU=Service,DC=example,DC=com",
    "bind_password": "encrypted-blob",
    "base_dn": "DC=example,DC=com",
    "user_search_filter": "(sAMAccountName={0})",
    "user_search_base": None,
    "group_search_filter": None,
    "group_search_base": None,
    "group_membership_attr": "memberOf",
    "username_attr": "sAMAccountName",
    "display_name_attr": "displayName",
    "email_attr": "mail",
    "connect_timeout_seconds": 5,
    "search_timeout_seconds": 10,
    "idle_timeout_seconds": 300,
    "tls_ca_cert": None,
    "tls_require_cert": "demand",
    "priority": 100,
    "enabled": True,
}


@pytest.mark.asyncio
async def test_ldap_server_insert_lookup_update(db_session, tenant_id):
    inserted = await ldap_repo.insert_ldap_server(
        db_session, tenant_id=tenant_id, fields=dict(_LDAP_BASE_FIELDS),
    )
    assert inserted["host"] == "ldap.example.com"
    assert inserted["tls_require_cert"] == "demand"
    server_id = UUID(str(inserted["id"]))

    fetched = await ldap_repo.lookup_ldap_server(
        db_session, tenant_id=tenant_id, server_id=server_id,
    )
    assert fetched is not None
    assert fetched["bind_dn"] == _LDAP_BASE_FIELDS["bind_dn"]

    # Update touching the renamed column (bind_password -> bind_password_encrypted)
    # and the enum-string column (tls_require_cert).
    updated = await ldap_repo.update_ldap_server(
        db_session,
        tenant_id=tenant_id,
        server_id=server_id,
        updates={"bind_password": "new-encrypted", "tls_require_cert": "never"},
    )
    assert updated is not None
    assert updated["tls_require_cert"] == "never"

    # The full-fat lookup (used by the test endpoint) returns the password.
    full = await ldap_repo.lookup_full_for_test(
        db_session, tenant_id=tenant_id, server_id=server_id,
    )
    assert full is not None
    assert full["bind_password_encrypted"] == "new-encrypted"


# ---------------------------------------------------------------------------
# vlans — exercises the CAST(:subnet AS cidr) form (PR #32 fix)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vlan_insert_with_cidr_subnet(db_session, tenant_id):
    """Subnet is a real CIDR cast — this is the exact pattern PR #32 fixed.

    If anyone reverts CAST(:subnet AS cidr) back to :subnet::cidr, asyncpg
    will reject this INSERT with 'syntax error at or near :'.
    """
    inserted = await vlan_repo.insert_vlan(
        db_session,
        tenant_id=tenant_id,
        vlan_id=100,
        name="users",
        description="Smoke test",
        purpose="user",
        subnet="10.10.0.0/24",
        enabled=True,
    )
    assert inserted["vlan_id"] == 100
    assert str(inserted["subnet"]) == "10.10.0.0/24"

    # Subnet=NULL must also work (the field is optional).
    inserted2 = await vlan_repo.insert_vlan(
        db_session,
        tenant_id=tenant_id,
        vlan_id=200,
        name="guests",
        description=None,
        purpose=None,
        subnet=None,
        enabled=True,
    )
    assert inserted2["subnet"] is None

    rows = await vlan_repo.list_vlans(db_session, tenant_id=tenant_id)
    assert {r["vlan_id"] for r in rows} >= {100, 200}
