"""HTTP routes for the backups feature (Layer 3)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import require_admin

from . import service
from .schemas import (
    BackupRunListResponse,
    BackupRunResponse,
    BackupSettingsResponse,
)

router = APIRouter(prefix="/backups")


@router.get("/settings", response_model=BackupSettingsResponse)
async def get_backup_settings(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Read the tenant's backup schedule + destination config.

    Returns Pydantic defaults if the operator has never visited the
    settings page. The encrypted credential blob is NEVER returned —
    `destination_configured: bool` indicates whether creds are
    present, but the values themselves are write-only via PUT
    (sub-PR 3).
    """
    return await service.get_settings(db, tenant_id=user["tenant_id"])


@router.get("/runs", response_model=BackupRunListResponse)
async def list_backup_runs(
    status: str | None = Query(
        None,
        pattern=r"^(pending|running|ok|error)$",
        description="Optional filter by local_status",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Paginated run history, newest first."""
    return await service.list_runs(
        db, tenant_id=user["tenant_id"],
        status=status, page=page, page_size=page_size,
    )


@router.get("/runs/{run_id}", response_model=BackupRunResponse)
async def get_backup_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """One run by id. 404 if unknown or wrong tenant."""
    return await service.get_run(
        db, tenant_id=user["tenant_id"], run_id=run_id,
    )
