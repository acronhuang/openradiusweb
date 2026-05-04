"""Database atoms for the backups feature.

Sub-PR 1 = read side only:
  - lookup_settings: returns the singleton row or None (callers
    fall back to Pydantic defaults via service layer)
  - count_runs / list_runs / lookup_run: history listing + detail

Writes (insert/upsert settings, insert run, update run progress)
land in sub-PR 2 (scheduler) + sub-PR 3 (PUT settings + manual
trigger).
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Columns excluded from public reads. The encrypted blob is
# write-only — service layer never returns it; only the booleans
# (`destination_configured`) derived from "is the column NULL".
_SETTINGS_PUBLIC_COLS = (
    "schedule_cron, keep_days, destination_type, "
    "destination_config_encrypted IS NOT NULL AS destination_configured, "
    "enabled, created_at, updated_at"
)


async def lookup_settings(
    db: AsyncSession, *, tenant_id: str,
) -> Optional[Mapping[str, Any]]:
    """Returns the singleton settings row for the tenant, or None.

    None means the operator has never visited the backup settings
    page yet; the service layer fills in Pydantic defaults so
    GET /backups/settings always returns a usable shape.
    """
    result = await db.execute(
        text(
            f"SELECT {_SETTINGS_PUBLIC_COLS} FROM backup_settings "
            "WHERE tenant_id = :tenant_id LIMIT 1"
        ),
        {"tenant_id": tenant_id},
    )
    return result.mappings().first()


async def count_runs(
    db: AsyncSession, *, tenant_id: str,
    status: Optional[str] = None,
) -> int:
    """Total run count for pagination. Optional filter by local_status
    (e.g. status='ok' to count just successful runs)."""
    if status is None:
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM backup_runs "
                "WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
    else:
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM backup_runs "
                "WHERE tenant_id = :tenant_id AND local_status = :status"
            ),
            {"tenant_id": tenant_id, "status": status},
        )
    return int(result.scalar() or 0)


async def list_runs(
    db: AsyncSession, *, tenant_id: str,
    status: Optional[str] = None,
    page: int = 1, page_size: int = 50,
) -> list[Mapping[str, Any]]:
    """Paginated run history, newest first. Optional status filter."""
    offset = (page - 1) * page_size
    if status is None:
        result = await db.execute(
            text(
                "SELECT * FROM backup_runs "
                "WHERE tenant_id = :tenant_id "
                "ORDER BY started_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {"tenant_id": tenant_id, "limit": page_size, "offset": offset},
        )
    else:
        result = await db.execute(
            text(
                "SELECT * FROM backup_runs "
                "WHERE tenant_id = :tenant_id AND local_status = :status "
                "ORDER BY started_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {
                "tenant_id": tenant_id, "status": status,
                "limit": page_size, "offset": offset,
            },
        )
    return list(result.mappings().all())


async def lookup_run(
    db: AsyncSession, *, tenant_id: str, run_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """One row by id, scoped to tenant — never lets one tenant read
    another's run history (defense-in-depth even though there's no
    multi-tenant traffic today)."""
    result = await db.execute(
        text(
            "SELECT * FROM backup_runs "
            "WHERE id = :id AND tenant_id = :tenant_id LIMIT 1"
        ),
        {"id": str(run_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()
