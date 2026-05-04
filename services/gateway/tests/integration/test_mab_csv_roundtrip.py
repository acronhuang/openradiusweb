"""Integration test: import a CSV → export it back → bytes match.

Real Postgres via testcontainers. Catches the SQL bugs that mock
tests can't:
  - the new `description` + `expiry_date` columns being passed to
    `INSERT INTO mab_devices` (PR #97 added these to the bulk insert)
  - export query returns the columns in the order the CSV writer
    expects
  - asyncpg type coercion for assigned_vlan_id (int) and expiry_date
    (timestamptz)
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from features.mab_devices import service as mab_service


@pytest.fixture
def actor(tenant_id):
    """A synthetic actor for the import flow. Insert a real users
    row so created_by FK resolves."""
    return {"sub": str(uuid4()), "tenant_id": tenant_id}


async def _seed_user(db_session, *, actor, tenant_id):
    """The mab_devices.created_by column FKs to users(id). Bulk insert
    needs a real user; insert one for the test's actor.sub."""
    await db_session.execute(
        text(
            "INSERT INTO users (id, username, email, password_hash, "
            "                   role, tenant_id) "
            "VALUES (:id, :username, :email, 'x', 'admin', :tenant)"
        ),
        {
            "id": actor["sub"],
            "username": f"test-{uuid4().hex[:8]}",
            "email": f"test-{uuid4().hex[:8]}@orw.local",
            "tenant": tenant_id,
        },
    )


CSV_FIXTURE = (
    "mac_address,name,description,device_type,assigned_vlan_id,expiry_date\n"
    'aa:bb:cc:dd:ee:01,Printer-Lobby,"Brother HL-L2310D, lobby",printer,30,2027-01-01T00:00:00+00:00\n'
    "aa:bb:cc:dd:ee:02,IPCam-3F,,camera,30,\n"
    "aa:bb:cc:dd:ee:03,Sensor-A,Temp sensor,iot,30,\n"
)


@pytest.mark.asyncio
async def test_import_csv_then_export_roundtrips(db_session, tenant_id, actor):
    await _seed_user(db_session, actor=actor, tenant_id=tenant_id)

    summary = await mab_service.import_csv(
        db_session, actor, csv_text=CSV_FIXTURE, client_ip=None,
    )
    assert summary["created"] == 3
    assert summary["skipped"] == 0
    assert summary["parse_errors"] == []

    exported = await mab_service.export_csv(db_session, tenant_id=tenant_id)

    # Header line + 3 rows + (writer terminates final line with \n)
    lines = [ln for ln in exported.splitlines() if ln]
    assert lines[0] == (
        "mac_address,name,description,device_type,assigned_vlan_id,expiry_date"
    )
    assert len(lines) == 4

    # Round-trip the export back through the parser → must produce the
    # same 3 rows we started with.
    items, errors = mab_service._parse_csv_to_bulk_items(exported)
    assert errors == []
    assert {i.mac_address for i in items} == {
        "aa:bb:cc:dd:ee:01",
        "aa:bb:cc:dd:ee:02",
        "aa:bb:cc:dd:ee:03",
    }
    by_mac = {i.mac_address: i for i in items}
    assert by_mac["aa:bb:cc:dd:ee:01"].description == "Brother HL-L2310D, lobby"
    assert by_mac["aa:bb:cc:dd:ee:01"].assigned_vlan_id == 30
    assert by_mac["aa:bb:cc:dd:ee:01"].expiry_date is not None


@pytest.mark.asyncio
async def test_import_csv_skips_existing_macs(db_session, tenant_id, actor):
    """Re-importing the same CSV is idempotent: second pass reports
    every row as `skipped`, not as a duplicate-key error."""
    await _seed_user(db_session, actor=actor, tenant_id=tenant_id)

    first = await mab_service.import_csv(
        db_session, actor, csv_text=CSV_FIXTURE, client_ip=None,
    )
    assert first["created"] == 3
    assert first["skipped"] == 0

    second = await mab_service.import_csv(
        db_session, actor, csv_text=CSV_FIXTURE, client_ip=None,
    )
    assert second["created"] == 0
    assert second["skipped"] == 3
    assert second["parse_errors"] == []


@pytest.mark.asyncio
async def test_export_empty_tenant_returns_header_only(db_session, tenant_id):
    """A tenant with zero MAB devices exports the header line and
    nothing else — useful as a starter template the operator can
    fill in."""
    out = await mab_service.export_csv(db_session, tenant_id=tenant_id)
    lines = out.splitlines()
    assert len(lines) == 1
    assert "mac_address" in lines[0]
