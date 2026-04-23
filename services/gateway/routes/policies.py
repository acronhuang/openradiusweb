"""Policy management routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.policy import (
    PolicyCreate, PolicyUpdate, PolicyResponse,
    PolicyTemplateOverrides, DeviceContext,
)
from orw_common import nats_client
from orw_common.policy_evaluator import PolicyEvaluator, POLICY_TEMPLATES, ACTION_TYPES
from middleware.auth import get_current_user, require_operator, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, POLICY_UPDATE_COLUMNS, POLICY_TYPE_CASTS

router = APIRouter(prefix="/policies")


@router.get("")
async def list_policies(
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List all policies."""
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM policies WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT * FROM policies WHERE {where} "
            f"ORDER BY priority ASC, name ASC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()

    return {
        "items": [PolicyResponse(**dict(r)) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", status_code=201)
async def create_policy(
    req: PolicyCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Create a new policy."""
    import json

    result = await db.execute(
        text(
            "INSERT INTO policies "
            "(name, description, priority, conditions, match_actions, "
            "no_match_actions, enabled, tenant_id, created_by) "
            "VALUES (:name, :description, :priority, :conditions::jsonb, "
            ":match_actions::jsonb, :no_match_actions::jsonb, :enabled, "
            ":tenant_id, :created_by) RETURNING *"
        ),
        {
            "name": req.name,
            "description": req.description,
            "priority": req.priority,
            "conditions": json.dumps([c.model_dump() for c in req.conditions]),
            "match_actions": json.dumps([a.model_dump() for a in req.match_actions]),
            "no_match_actions": json.dumps([a.model_dump() for a in req.no_match_actions]),
            "enabled": req.enabled,
            "tenant_id": user["tenant_id"],
            "created_by": user["sub"],
        },
    )
    policy = result.mappings().first()

    # Notify policy engine of new policy
    await nats_client.publish("orw.policy.created", {
        "policy_id": str(policy["id"]),
        "name": req.name,
    })

    await log_audit(db, user, "create", "policy", str(policy["id"]),
                    {"name": req.name, "priority": req.priority})

    return PolicyResponse(**dict(policy))


# ============================================================
# Policy Templates & Simulation (static routes before {policy_id})
# ============================================================

@router.get("/templates/list")
async def list_policy_templates(
    user: dict = Depends(get_current_user),
):
    """List available policy templates for quick setup."""
    templates = []
    for key, tmpl in POLICY_TEMPLATES.items():
        templates.append({
            "template_id": key,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "priority": tmpl["priority"],
            "conditions_count": len(tmpl["conditions"]),
            "actions_count": len(tmpl["match_actions"]),
            "conditions": tmpl["conditions"],
            "match_actions": tmpl["match_actions"],
            "no_match_actions": tmpl["no_match_actions"],
        })

    return {
        "templates": sorted(templates, key=lambda t: t["priority"]),
        "action_types": {k: {"description": v["description"]} for k, v in ACTION_TYPES.items()},
    }


@router.post("/templates/{template_id}/apply")
async def apply_policy_template(
    template_id: str,
    overrides: PolicyTemplateOverrides | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Create a policy from a template, optionally overriding fields."""
    import json

    if template_id not in POLICY_TEMPLATES:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    tmpl = POLICY_TEMPLATES[template_id].copy()

    # Apply overrides
    if overrides:
        override_data = overrides.model_dump(exclude_none=True)
        for key in ("name", "description", "priority", "conditions", "match_actions", "no_match_actions"):
            if key in override_data:
                tmpl[key] = override_data[key]

    result = await db.execute(
        text(
            "INSERT INTO policies "
            "(name, description, priority, conditions, match_actions, "
            "no_match_actions, enabled, tenant_id, created_by) "
            "VALUES (:name, :description, :priority, :conditions::jsonb, "
            ":match_actions::jsonb, :no_match_actions::jsonb, true, "
            ":tenant_id, :created_by) RETURNING *"
        ),
        {
            "name": tmpl["name"],
            "description": tmpl["description"],
            "priority": tmpl["priority"],
            "conditions": json.dumps(tmpl["conditions"]),
            "match_actions": json.dumps(tmpl["match_actions"]),
            "no_match_actions": json.dumps(tmpl.get("no_match_actions", [])),
            "tenant_id": user["tenant_id"],
            "created_by": user["sub"],
        },
    )
    policy = result.mappings().first()
    return PolicyResponse(**dict(policy))


@router.post("/simulate-all")
async def simulate_all_policies(
    device_context: DeviceContext,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Simulate ALL enabled policies against a device context.
    Shows which policy would be the first match (winning policy).
    """

    ctx = device_context.model_dump(exclude_none=True)

    # Get all enabled policies ordered by priority
    result = await db.execute(
        text(
            "SELECT * FROM policies "
            "WHERE enabled = true AND tenant_id = :tenant_id "
            "ORDER BY priority ASC"
        ),
        {"tenant_id": user["tenant_id"]},
    )
    policies = result.mappings().all()

    evaluator = PolicyEvaluator()
    results = []
    winning_policy = None

    for policy in policies:
        eval_result = evaluator.evaluate_with_details(dict(policy), ctx)
        results.append(eval_result)
        if eval_result["matched"] and winning_policy is None:
            winning_policy = {
                "policy_id": str(policy["id"]),
                "policy_name": policy["name"],
                "priority": policy["priority"],
                "actions": policy["match_actions"],
            }

    return {
        "device_context": ctx,
        "winning_policy": winning_policy,
        "all_evaluations": results,
        "total_policies": len(policies),
    }


# ============================================================
# Dynamic {policy_id} routes (must come after static routes)
# ============================================================

@router.get("/{policy_id}")
async def get_policy(
    policy_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific policy."""
    result = await db.execute(
        text(
            "SELECT * FROM policies WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(policy_id), "tenant_id": user["tenant_id"]},
    )
    policy = result.mappings().first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return PolicyResponse(**dict(policy))


@router.patch("/{policy_id}")
async def update_policy(
    policy_id: UUID,
    req: PolicyUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Update a policy."""
    import json

    raw = req.model_dump(exclude_unset=True)
    updates = {}
    for k, v in raw.items():
        if v is not None:
            if k in ("conditions", "match_actions", "no_match_actions"):
                updates[k] = json.dumps([item.model_dump() if hasattr(item, "model_dump") else item for item in v])
            else:
                updates[k] = v

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        set_clause, params = build_safe_set_clause(
            updates, POLICY_UPDATE_COLUMNS, type_casts=POLICY_TYPE_CASTS
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(policy_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE policies SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
        ),
        params,
    )
    policy = result.mappings().first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    await nats_client.publish("orw.policy.updated", {
        "policy_id": str(policy["id"]),
    })

    await log_audit(db, user, "update", "policy", str(policy["id"]),
                    {"changed_fields": list(raw.keys())})

    return PolicyResponse(**dict(policy))


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a policy (admin only)."""
    result = await db.execute(
        text(
            "DELETE FROM policies WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING id"
        ),
        {"id": str(policy_id), "tenant_id": user["tenant_id"]},
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="Policy not found")

    await nats_client.publish("orw.policy.deleted", {
        "policy_id": str(policy_id),
    })

    await log_audit(db, user, "delete", "policy", str(policy_id))


@router.post("/{policy_id}/simulate")
async def simulate_policy(
    policy_id: UUID,
    device_context: DeviceContext,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Simulate a policy against a device context.
    Returns detailed per-condition evaluation results.
    Useful for testing policies before enabling them.
    """

    # Get policy
    result = await db.execute(
        text(
            "SELECT * FROM policies WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(policy_id), "tenant_id": user["tenant_id"]},
    )
    policy = result.mappings().first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    # Run simulation
    evaluator = PolicyEvaluator()
    ctx = device_context.model_dump(exclude_none=True)
    eval_result = evaluator.evaluate_with_details(dict(policy), ctx)

    # Determine which actions would execute
    if eval_result["matched"]:
        actions = policy["match_actions"]
    else:
        actions = policy["no_match_actions"]

    return {
        "evaluation": eval_result,
        "would_execute_actions": actions,
    }
