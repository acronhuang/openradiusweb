"""Use-case composition for the backups feature.

Sub-PR 1: read-only views.
  - get_settings: returns the tenant's settings row, falling back to
    Pydantic defaults if no row exists yet
  - list_runs / get_run: paginated history + per-run detail

Domain exceptions raised:
  - NotFoundError when a run_id is unknown
"""
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError
from orw_common.models.backup import (
    BackupRunListResponse,
    BackupRunResponse,
    BackupSettingsResponse,
)

from . import repository as repo


async def get_settings(
    db: AsyncSession, *, tenant_id: str,
) -> BackupSettingsResponse:
    """Returns the tenant's backup_settings row.

    If no row exists (operator hasn't visited the page), returns a
    BackupSettingsResponse populated from Pydantic defaults so the UI
    always has something to render. Sub-PR 3's PUT will create the
    row on first save.
    """
    row = await repo.lookup_settings(db, tenant_id=tenant_id)
    if row is None:
        return BackupSettingsResponse()  # all defaults
    return BackupSettingsResponse(**dict(row))


async def list_runs(
    db: AsyncSession, *, tenant_id: str,
    status: Optional[str] = None,
    page: int = 1, page_size: int = 50,
) -> BackupRunListResponse:
    """Paginated run history. Optional status filter (one of
    'pending', 'running', 'ok', 'error')."""
    items_raw = await repo.list_runs(
        db, tenant_id=tenant_id, status=status,
        page=page, page_size=page_size,
    )
    total = await repo.count_runs(
        db, tenant_id=tenant_id, status=status,
    )
    return BackupRunListResponse(
        items=[BackupRunResponse(**dict(r)) for r in items_raw],
        total=total, page=page, page_size=page_size,
    )


async def get_run(
    db: AsyncSession, *, tenant_id: str, run_id: UUID,
) -> BackupRunResponse:
    row = await repo.lookup_run(db, tenant_id=tenant_id, run_id=run_id)
    if row is None:
        raise NotFoundError("BackupRun", str(run_id))
    return BackupRunResponse(**dict(row))
