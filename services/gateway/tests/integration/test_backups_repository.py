"""Integration tests for the backups repository against real Postgres.

Catches the SQL bug class PR #94 / #97 hit (asyncpg type coercion,
column reference typos, etc.). Migration 007 is applied by the
test container's conftest globbing.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from features.backups import repository as repo


# ---------------------------------------------------------------------------
# Settings — singleton-per-tenant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_settings_returns_none_when_no_row(db_session, tenant_id):
    """Fresh tenant → no settings row yet → service must fall back
    to defaults. The repo just returns None."""
    out = await repo.lookup_settings(db_session, tenant_id=tenant_id)
    assert out is None


@pytest.mark.asyncio
async def test_lookup_settings_omits_encrypted_blob(db_session, tenant_id):
    """The public column list intentionally excludes
    destination_config_encrypted (the credential blob). It surfaces
    only as a `destination_configured` boolean. Verify by inserting
    a row with non-NULL encrypted blob and confirming the SELECT
    returns the boolean but NOT the blob itself."""
    await db_session.execute(
        text(
            "INSERT INTO backup_settings "
            "(tenant_id, schedule_cron, destination_type, "
            " destination_config_encrypted, enabled) "
            "VALUES (:tid, '0 4 * * *', 'rsync', 'fake-ciphertext', true)"
        ),
        {"tid": tenant_id},
    )
    out = await repo.lookup_settings(db_session, tenant_id=tenant_id)
    assert out is not None
    assert out["schedule_cron"] == "0 4 * * *"
    assert out["destination_type"] == "rsync"
    assert out["destination_configured"] is True
    assert out["enabled"] is True
    # The encrypted blob must NOT be in the result mapping
    assert "destination_config_encrypted" not in out


@pytest.mark.asyncio
async def test_settings_destination_configured_false_when_blob_null(
    db_session, tenant_id,
):
    await db_session.execute(
        text(
            "INSERT INTO backup_settings (tenant_id) VALUES (:tid)"
        ),
        {"tid": tenant_id},
    )
    out = await repo.lookup_settings(db_session, tenant_id=tenant_id)
    assert out["destination_configured"] is False


# ---------------------------------------------------------------------------
# Runs — listing + pagination + filter
# ---------------------------------------------------------------------------

async def _insert_run(db_session, tenant_id, *,
                     local_status="ok",
                     started_offset_seconds=0):
    """Insert a backup_runs row with controlled started_at offset
    (negative = older)."""
    rid = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO backup_runs "
            "(id, tenant_id, triggered_by, started_at, local_status, "
            " local_archive_size_bytes, prune_deleted_count) "
            "VALUES (:id, :tid, 'schedule', "
            "        NOW() + (:offset || ' seconds')::interval, "
            "        :status, 90000, 0)"
        ),
        {"id": str(rid), "tid": tenant_id,
         "offset": str(started_offset_seconds), "status": local_status},
    )
    return rid


@pytest.mark.asyncio
async def test_list_runs_returns_newest_first(db_session, tenant_id):
    a = await _insert_run(db_session, tenant_id, started_offset_seconds=-300)
    b = await _insert_run(db_session, tenant_id, started_offset_seconds=-100)
    c = await _insert_run(db_session, tenant_id, started_offset_seconds=-200)

    rows = await repo.list_runs(db_session, tenant_id=tenant_id)
    ids_ordered = [r["id"] for r in rows]
    # b is most recent (-100s), then c (-200s), then a (-300s)
    assert ids_ordered == [b, c, a]


@pytest.mark.asyncio
async def test_list_runs_status_filter(db_session, tenant_id):
    await _insert_run(db_session, tenant_id, local_status="ok")
    await _insert_run(db_session, tenant_id, local_status="ok")
    await _insert_run(db_session, tenant_id, local_status="error")

    ok_rows = await repo.list_runs(db_session, tenant_id=tenant_id, status="ok")
    err_rows = await repo.list_runs(db_session, tenant_id=tenant_id, status="error")
    assert len(ok_rows) == 2
    assert len(err_rows) == 1


@pytest.mark.asyncio
async def test_count_runs_matches_filter(db_session, tenant_id):
    await _insert_run(db_session, tenant_id, local_status="ok")
    await _insert_run(db_session, tenant_id, local_status="error")
    assert await repo.count_runs(db_session, tenant_id=tenant_id) == 2
    assert await repo.count_runs(db_session, tenant_id=tenant_id, status="ok") == 1


@pytest.mark.asyncio
async def test_pagination_offset(db_session, tenant_id):
    """page=2 page_size=2 should skip the first 2 newest rows."""
    ids = []
    for i in range(5):
        ids.append(await _insert_run(
            db_session, tenant_id, started_offset_seconds=-i * 10,
        ))
    # Newest first: ids[0] (offset 0) is most recent
    page2 = await repo.list_runs(
        db_session, tenant_id=tenant_id, page=2, page_size=2,
    )
    page2_ids = [r["id"] for r in page2]
    # Page 1 = ids[0], ids[1]; page 2 = ids[2], ids[3]
    assert page2_ids == [ids[2], ids[3]]


@pytest.mark.asyncio
async def test_lookup_run_scoped_to_tenant(db_session, tenant_id):
    """Cross-tenant defense: a run from tenant A must not be
    readable as tenant B."""
    rid = await _insert_run(db_session, tenant_id)

    # Make a second tenant
    other = await db_session.execute(
        text(
            "INSERT INTO tenants (name, display_name) "
            "VALUES (:n, :n) RETURNING id"
        ),
        {"n": f"other-{uuid4().hex[:8]}"},
    )
    other_tid = str(other.scalar())

    # Same run id, wrong tenant → None
    cross = await repo.lookup_run(
        db_session, tenant_id=other_tid, run_id=rid,
    )
    assert cross is None

    # Correct tenant → present
    own = await repo.lookup_run(
        db_session, tenant_id=tenant_id, run_id=rid,
    )
    assert own is not None
    assert own["id"] == rid
