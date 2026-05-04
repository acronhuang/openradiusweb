"""Backup management feature.

Public contract:
- ``backups_router``: APIRouter for ``/backups/*`` endpoints
  (settings GET, run history GET).

Sub-PR 1 ships read-only. Sub-PRs 2-6 add scheduler, write side,
download, frontend (per docs/design-backup-ui.md).
"""
from .routes import router as backups_router

__all__ = ["backups_router"]
