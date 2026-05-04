"""Integration tests for the certificates repository — focus on the
queries that contain non-trivial PG-specific syntax (intervals, casts,
arrays) where mock-based unit tests can't catch the runtime errors.

PR #94 added these because PR #93 shipped with a working mock test
but a runtime-broken SQL query: `($2 || ' days')::interval` made
asyncpg encode the int parameter as text and raise
`DataError: expected str, got int`. Caught only when the gateway's
background task actually executed it on prod.

This test layer is the prevention: every new repo function that
includes interval arithmetic, casts, or any PG-specific construct
should have at least one test here that runs the actual SQL against
real Postgres.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from features.certificates import repository as cert_repo


# ---------------------------------------------------------------------------
# list_renewable_server_certs_within
# ---------------------------------------------------------------------------

async def _insert_server_cert(
    db_session, *, tenant_id: str, name: str,
    not_before: datetime, not_after: datetime,
    is_active: bool = True, imported: bool = False,
):
    """Minimal cert row insert that satisfies NOT NULL columns. We
    bypass the service layer to control the timestamps directly."""
    await db_session.execute(
        text(
            "INSERT INTO certificates "
            "(cert_type, name, common_name, pem_data, "
            " not_before, not_after, key_size, "
            " is_active, enabled, imported, tenant_id, "
            " subject_alt_names) "
            "VALUES "
            "('server', :name, :cn, :pem, "
            " :nb, :na, 2048, "
            " :active, true, :imported, :tenant, "
            " :sans)"
        ),
        {
            "name": name, "cn": f"{name}.test.local",
            "pem": "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n",
            "nb": not_before, "na": not_after,
            "active": is_active, "imported": imported,
            "tenant": tenant_id,
            "sans": [f"{name}.test.local", "10.0.0.1"],
        },
    )


@pytest.mark.asyncio
async def test_list_renewable_with_int_threshold_does_not_raise(
    db_session, tenant_id,
):
    """The exact failure shape from prod: passing threshold_days as a
    natural Python int must work end-to-end. Pre-PR-#94 this raised
    `DataError: expected str, got int` from asyncpg's parameter
    encoder because the query used `(:days || ' days')::interval`."""
    now = datetime.now(timezone.utc)
    await _insert_server_cert(
        db_session, tenant_id=tenant_id,
        name="prod-style-cert",
        not_before=now - timedelta(days=720),
        not_after=now + timedelta(days=10),
    )

    rows = await cert_repo.list_renewable_server_certs_within(
        db_session, tenant_id=tenant_id, threshold_days=30,
    )
    # The cert is expiring in 10 days, threshold is 30 → should be
    # picked up. Earlier the query raised before reaching this assert.
    assert any(r["name"] == "prod-style-cert" for r in rows)


@pytest.mark.asyncio
async def test_list_renewable_excludes_certs_outside_threshold(
    db_session, tenant_id,
):
    now = datetime.now(timezone.utc)
    await _insert_server_cert(
        db_session, tenant_id=tenant_id,
        name="far-future-cert",
        not_before=now,
        not_after=now + timedelta(days=400),
    )
    rows = await cert_repo.list_renewable_server_certs_within(
        db_session, tenant_id=tenant_id, threshold_days=30,
    )
    assert all(r["name"] != "far-future-cert" for r in rows)


@pytest.mark.asyncio
async def test_list_renewable_excludes_imported_certs(
    db_session, tenant_id,
):
    """Imported certs must be skipped — we don't have the original
    CSR/key context to renew them."""
    now = datetime.now(timezone.utc)
    await _insert_server_cert(
        db_session, tenant_id=tenant_id,
        name="imported-expiring-cert",
        not_before=now - timedelta(days=720),
        not_after=now + timedelta(days=5),
        imported=True,
    )
    rows = await cert_repo.list_renewable_server_certs_within(
        db_session, tenant_id=tenant_id, threshold_days=30,
    )
    assert all(r["name"] != "imported-expiring-cert" for r in rows)


@pytest.mark.asyncio
async def test_list_renewable_excludes_inactive_certs(
    db_session, tenant_id,
):
    now = datetime.now(timezone.utc)
    await _insert_server_cert(
        db_session, tenant_id=tenant_id,
        name="inactive-expiring-cert",
        not_before=now - timedelta(days=720),
        not_after=now + timedelta(days=5),
        is_active=False,
    )
    rows = await cert_repo.list_renewable_server_certs_within(
        db_session, tenant_id=tenant_id, threshold_days=30,
    )
    assert all(r["name"] != "inactive-expiring-cert" for r in rows)


@pytest.mark.asyncio
async def test_list_renewable_orders_by_not_after(
    db_session, tenant_id,
):
    now = datetime.now(timezone.utc)
    await _insert_server_cert(
        db_session, tenant_id=tenant_id, name="cert-later",
        not_before=now, not_after=now + timedelta(days=20),
    )
    await _insert_server_cert(
        db_session, tenant_id=tenant_id, name="cert-soonest",
        not_before=now, not_after=now + timedelta(days=2),
    )
    rows = await cert_repo.list_renewable_server_certs_within(
        db_session, tenant_id=tenant_id, threshold_days=30,
    )
    names = [r["name"] for r in rows if r["name"].startswith("cert-")]
    assert names == ["cert-soonest", "cert-later"]
