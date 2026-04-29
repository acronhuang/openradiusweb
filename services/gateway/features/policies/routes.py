"""HTTP layer for the policies feature.

Thin handlers — parse → call service → wrap in PolicyResponse.
RBAC dependencies (require_operator, require_admin) stay here so the
auth contract is visible alongside the route definition.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.auth import get_current_user, require_admin, require_operator
from orw_common.database import get_db
from orw_common.models.policy import (
    DeviceContext,
    PolicyCreate,
    PolicyResponse,
    PolicyTemplateOverrides,
    PolicyUpdate,
)

from . import service

router = APIRouter(prefix="/policies")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_policies(
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    out = await service.list_policies(
        db,
        tenant_id=user["tenant_id"],
        enabled=enabled, page=page, page_size=page_size,
    )
    return {
        **out,
        "items": [PolicyResponse(**r) for r in out["items"]],
    }


@router.post("", status_code=201)
async def create_policy(
    req: PolicyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    row = await service.create_policy(
        db, user, req=req, client_ip=_client_ip(request),
    )
    return PolicyResponse(**row)


# ---------------------------------------------------------------------------
# Templates + simulation (static paths before {policy_id})
# ---------------------------------------------------------------------------

@router.get("/templates/list")
async def list_policy_templates(
    user: dict = Depends(get_current_user),
):
    return service.list_templates()


@router.post("/templates/{template_id}/apply")
async def apply_policy_template(
    template_id: str,
    overrides: PolicyTemplateOverrides | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    row = await service.apply_template(
        db, user, template_id=template_id, overrides=overrides,
    )
    return PolicyResponse(**row)


@router.post("/simulate-all")
async def simulate_all_policies(
    device_context: DeviceContext,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.simulate_all_policies(
        db,
        tenant_id=user["tenant_id"],
        device_context=device_context.model_dump(exclude_none=True),
    )


# ---------------------------------------------------------------------------
# Dynamic {policy_id} routes
# ---------------------------------------------------------------------------

@router.get("/{policy_id}")
async def get_policy(
    policy_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    row = await service.get_policy(
        db, tenant_id=user["tenant_id"], policy_id=policy_id,
    )
    return PolicyResponse(**row)


@router.patch("/{policy_id}")
async def update_policy(
    policy_id: UUID,
    req: PolicyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    row = await service.update_policy(
        db, user,
        policy_id=policy_id, req=req,
        client_ip=_client_ip(request),
    )
    return PolicyResponse(**row)


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    await service.delete_policy(
        db, user, policy_id=policy_id, client_ip=_client_ip(request),
    )


@router.post("/{policy_id}/simulate")
async def simulate_policy(
    policy_id: UUID,
    device_context: DeviceContext,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.simulate_policy(
        db,
        tenant_id=user["tenant_id"],
        policy_id=policy_id,
        device_context=device_context.model_dump(exclude_none=True),
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
