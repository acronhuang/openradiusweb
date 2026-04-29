"""Use-case composition for the radius_auth_log feature (Layer 2).

8 use cases:
  - list_logs / get_log_detail
  - get_summary_stats / get_stats_by_nas / get_stats_by_failure_category
  - list_failure_catalog / get_live_feed
  - export_logs (returns dicts; CSV serialization stays at routes layer)

Domain exceptions raised:
  - NotFoundError when an explicit log_id does not exist
  - ValidationError when an export window is missing
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError

from . import repository as repo


# ---------------------------------------------------------------------------
# list_logs — pagination + filters
# ---------------------------------------------------------------------------

async def list_logs(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    filters: Mapping[str, Any],
) -> dict[str, Any]:
    sort_by = sort_by if sort_by in repo.ALLOWED_SORT_COLUMNS else "timestamp"
    sort_order = "asc" if sort_order.lower() == "asc" else "desc"

    normalized = _normalize_filters(filters)

    total = await repo.count_logs(db, normalized)
    items = await repo.list_logs(
        db, normalized,
        sort_by=sort_by, sort_order=sort_order,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


def _normalize_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    """Translate ``last_hours`` to a since-timestamp; pass others through."""
    out = {k: v for k, v in filters.items() if k != "last_hours"}
    last_hours = filters.get("last_hours")
    if last_hours:
        out["since"] = datetime.now(timezone.utc) - timedelta(hours=last_hours)
        # since wins over start_time/end_time per legacy behavior
        out.pop("start_time", None)
        out.pop("end_time", None)
    return out


# ---------------------------------------------------------------------------
# get_log_detail — entry + troubleshooting + related history
# ---------------------------------------------------------------------------

async def get_log_detail(
    db: AsyncSession, *, log_id: UUID,
) -> dict[str, Any]:
    entry = await repo.get_log_by_id(db, log_id)
    if not entry:
        raise NotFoundError("Auth log entry not found")

    troubleshooting = await _resolve_troubleshooting(db, entry)

    related: list[dict[str, Any]] = []
    if entry.get("calling_station_id"):
        related = await repo.list_related_by_mac(
            db, mac=entry["calling_station_id"], exclude_id=log_id,
        )

    return {
        "entry": entry,
        "troubleshooting": troubleshooting,
        "related_history": related,
    }


async def _resolve_troubleshooting(
    db: AsyncSession, entry: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Look up catalog entry for failed auths with a recorded failure_reason."""
    if entry.get("auth_result") == "success" or not entry.get("failure_reason"):
        return None

    code = entry.get("ad_error_code") or entry.get("failure_reason", "")
    desc_keyword = (entry.get("failure_reason") or "")[:50]
    catalog = await repo.find_failure_catalog_entry(
        db, code=code, description_keyword=desc_keyword,
    )
    if not catalog:
        return None
    return {
        "category": catalog["category"],
        "description": catalog["description"],
        "possible_causes": catalog["possible_causes"],
        "remediation_steps": catalog["remediation_steps"],
        "severity": catalog["severity"],
        "kb_url": catalog.get("kb_url"),
    }


# ---------------------------------------------------------------------------
# Stats: summary
# ---------------------------------------------------------------------------

async def get_summary_stats(
    db: AsyncSession, *, last_hours: int,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=last_hours)

    by_result = await repo.count_by_result(db, since=since)
    top_failures = await repo.top_failure_reasons(db, since=since)
    top_users = await repo.top_failing_users(db, since=since)
    top_macs = await repo.top_failing_macs(db, since=since)
    by_method = await repo.auth_method_distribution(db, since=since)
    trend = await repo.hourly_trend(db, since=since)

    total = sum(by_result.values())
    success = by_result.get("success", 0)
    return {
        "period_hours": last_hours,
        "total_attempts": total,
        "success_count": success,
        "failure_count": total - success,
        "success_rate": round(success / total * 100, 1) if total > 0 else 0,
        "by_result": by_result,
        "top_failure_reasons": top_failures,
        "top_failing_users": top_users,
        "top_failing_macs": top_macs,
        "by_auth_method": by_method,
        "hourly_trend": trend,
    }


async def get_stats_by_nas(
    db: AsyncSession, *, last_hours: int,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=last_hours)
    items = await repo.stats_by_nas(db, since=since)
    return {"items": items, "period_hours": last_hours}


async def get_stats_by_failure_category(
    db: AsyncSession, *, last_hours: int,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=last_hours)
    items = await repo.stats_by_failure_category(db, since=since)
    return {"items": items, "period_hours": last_hours}


# ---------------------------------------------------------------------------
# Catalog / live feed / export
# ---------------------------------------------------------------------------

async def list_failure_catalog(
    db: AsyncSession,
    *,
    category: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    items = await repo.list_failure_catalog(
        db, category=category, search=search,
    )
    return {"items": items}


async def get_live_feed(
    db: AsyncSession, *, last_seconds: int,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(seconds=last_seconds)
    items = await repo.live_feed(db, since=since)
    return {"items": items, "since": since.isoformat()}


async def export_logs(
    db: AsyncSession,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    auth_result: str | None = None,
) -> list[dict[str, Any]]:
    """Returns raw rows; CSV vs JSON serialization is the route's concern."""
    if not start_time or not end_time:
        raise ValidationError("start_time and end_time are required for export")
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
    }
    if auth_result:
        filters["auth_result"] = auth_result
    return await repo.list_logs_for_export(db, filters)
