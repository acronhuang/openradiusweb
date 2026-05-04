"""Backup management models (Phase 2 — see docs/design-backup-ui.md).

Sub-PR 1 ships read-only response models. Sub-PR 3 will add the
write-side `BackupSettingsUpdate` and the destination-config
discriminated union for PUT /backups/settings.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# Mirrors the CHECK constraint on backup_settings.destination_type.
DestinationType = Literal["none", "rsync", "local"]

# Mirrors the CHECK constraints on backup_runs.local_status / offsite_status.
RunStatus = Literal["pending", "running", "ok", "error"]
OffsiteStatus = Literal["skipped", "pending", "running", "ok", "error"]
TriggerSource = Literal["schedule", "manual", "api"]


class BackupSettingsResponse(BaseModel):
    """Read-only view of backup_settings.

    Deliberately does NOT include `destination_config_encrypted` —
    operators get a `destination_configured` boolean indicating
    whether creds are present, but the credential blob itself is
    write-only via PUT (sub-PR 3). This keeps SSH private keys /
    cloud secret keys out of GET responses entirely.

    The `destination_config_redacted` field (sub-PR 3) will surface
    the non-secret subset (e.g. rsync host + path, but not the SSH
    private key) so the UI can show "currently sending to
    nas.local:/srv" without leaking the key.
    """
    schedule_cron: str = Field(default="30 2 * * *")
    keep_days: int = Field(default=7, ge=1)
    destination_type: DestinationType = Field(default="none")
    destination_configured: bool = Field(
        default=False,
        description=(
            "True iff destination_config_encrypted is non-NULL. "
            "Tells the UI whether to show 'Configured' vs 'Not yet "
            "set up' next to the destination type."
        ),
    )
    enabled: bool = Field(default=False)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class BackupRunResponse(BaseModel):
    """One row from the backup_runs history table."""
    id: UUID
    triggered_by: TriggerSource
    triggered_user_id: Optional[UUID] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    local_status: RunStatus
    local_archive_path: Optional[str] = None
    local_archive_size_bytes: Optional[int] = None
    local_error: Optional[str] = None
    offsite_status: Optional[OffsiteStatus] = None
    offsite_error: Optional[str] = None
    prune_deleted_count: int = 0

    model_config = {"from_attributes": True}


class BackupRunListResponse(BaseModel):
    """Paginated history listing."""
    items: list[BackupRunResponse]
    total: int
    page: int
    page_size: int
