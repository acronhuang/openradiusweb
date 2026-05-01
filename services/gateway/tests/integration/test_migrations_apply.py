"""Smoke test: every migration applies on a fresh DB.

This is the lowest layer — no repository code, no fixtures beyond the
postgres container itself. If a future migration drops a column another
relies on, references a non-existent function, or the SQL is syntactically
invalid, the postgres_url fixture will fail to set up and this test will
be the first one red in CI.

Also asserts the expected core tables exist post-migration so a silent
DROP-but-no-CREATE bug doesn't slip through.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text


EXPECTED_TABLES = {
    "ldap_servers",
    "radius_nas_clients",
    "radius_realms",
    "mab_devices",
    "policies",
    "vlans",
    "group_vlan_mappings",
    "audit_log",
    "events",
    "radius_auth_log",
}


@pytest.mark.asyncio
async def test_all_expected_tables_exist(db_session):
    """If migrations ran, these tables must be present."""
    result = await db_session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    )
    actual = {row[0] for row in result.all()}
    missing = EXPECTED_TABLES - actual
    assert not missing, (
        f"Migrations applied but these tables are missing: {sorted(missing)}\n"
        f"Found: {sorted(actual)[:20]}..."
    )


@pytest.mark.asyncio
async def test_timescale_hypertables_registered(db_session):
    """The three hypertables in init.sql must actually be hypertables.

    create_hypertable() succeeding silently isn't enough — verify
    timescale knows about them.
    """
    result = await db_session.execute(
        text("SELECT hypertable_name FROM timescaledb_information.hypertables")
    )
    hypertables = {row[0] for row in result.all()}
    assert {"events", "audit_log", "radius_auth_log"}.issubset(hypertables), (
        f"Expected events/audit_log/radius_auth_log to be hypertables, "
        f"got: {sorted(hypertables)}"
    )
