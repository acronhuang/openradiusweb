"""Use-case composition for the policies feature (Layer 2).

Use cases:
  - list / get / create / update / delete (CRUD over policies table)
  - apply_template (insert a policy from a static catalog)
  - simulate_policy / simulate_all_policies (no DB writes)

The PolicyEvaluator and template catalog (POLICY_TEMPLATES /
ACTION_TYPES) live in ``orw_common.policy_evaluator`` so they can be
shared with the standalone policy_engine service.

Domain exceptions:
  - NotFoundError when a policy_id or template_id is unknown
  - ValidationError when an update has no allowed fields
"""
import json
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError
from orw_common.models.policy import (
    PolicyCreate,
    PolicyTemplateOverrides,
    PolicyUpdate,
)
from orw_common.policy_evaluator import (
    ACTION_TYPES,
    POLICY_TEMPLATES,
    PolicyEvaluator,
)
from utils.audit import log_audit

from . import events
from . import repository as repo


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_policies(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    total = await repo.count_policies(
        db, tenant_id=tenant_id, enabled=enabled,
    )
    rows = await repo.list_policies(
        db, tenant_id=tenant_id, enabled=enabled,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_policy(
    db: AsyncSession, *, tenant_id: str, policy_id: UUID,
) -> dict:
    row = await repo.lookup_policy(
        db, tenant_id=tenant_id, policy_id=policy_id,
    )
    if not row:
        raise NotFoundError("Policy", str(policy_id))
    return dict(row)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_policy(
    db: AsyncSession,
    actor: dict,
    *,
    req: PolicyCreate,
    client_ip: Optional[str] = None,
) -> dict:
    row = await repo.insert_policy(
        db,
        tenant_id=actor["tenant_id"],
        created_by=actor["sub"],
        name=req.name,
        description=req.description,
        priority=req.priority,
        conditions_json=_dump_models(req.conditions),
        match_actions_json=_dump_models(req.match_actions),
        no_match_actions_json=_dump_models(req.no_match_actions),
        enabled=req.enabled,
    )
    await events.publish_policy_created(
        policy_id=str(row["id"]), name=req.name,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="policy",
        resource_id=str(row["id"]),
        details={"name": req.name, "priority": req.priority},
        ip_address=client_ip,
    )
    return dict(row)


async def update_policy(
    db: AsyncSession,
    actor: dict,
    *,
    policy_id: UUID,
    req: PolicyUpdate,
    client_ip: Optional[str] = None,
) -> dict:
    raw = req.model_dump(exclude_unset=True)
    updates: dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        if k in ("conditions", "match_actions", "no_match_actions"):
            updates[k] = _dump_models(v)
        else:
            updates[k] = v
    if not updates:
        raise ValidationError("No fields to update")

    try:
        row = await repo.update_policy(
            db,
            tenant_id=actor["tenant_id"],
            policy_id=policy_id,
            updates=updates,
        )
    except ValueError as exc:
        raise ValidationError("No valid fields to update") from exc
    if not row:
        raise NotFoundError("Policy", str(policy_id))

    await events.publish_policy_updated(policy_id=str(row["id"]))
    await log_audit(
        db, actor,
        action="update", resource_type="policy",
        resource_id=str(row["id"]),
        details={"changed_fields": list(raw.keys())},
        ip_address=client_ip,
    )
    return dict(row)


async def delete_policy(
    db: AsyncSession,
    actor: dict,
    *,
    policy_id: UUID,
    client_ip: Optional[str] = None,
) -> None:
    deleted = await repo.delete_policy(
        db, tenant_id=actor["tenant_id"], policy_id=policy_id,
    )
    if not deleted:
        raise NotFoundError("Policy", str(policy_id))
    await events.publish_policy_deleted(policy_id=str(policy_id))
    await log_audit(
        db, actor,
        action="delete", resource_type="policy",
        resource_id=str(policy_id),
        ip_address=client_ip,
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def list_templates() -> dict[str, Any]:
    """Static catalog — no DB, no auth scope."""
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
        "action_types": {
            k: {"description": v["description"]}
            for k, v in ACTION_TYPES.items()
        },
    }


async def apply_template(
    db: AsyncSession,
    actor: dict,
    *,
    template_id: str,
    overrides: Optional[PolicyTemplateOverrides] = None,
) -> dict:
    if template_id not in POLICY_TEMPLATES:
        raise NotFoundError("Template", template_id)
    tmpl = POLICY_TEMPLATES[template_id].copy()

    if overrides:
        override_data = overrides.model_dump(exclude_none=True)
        for key in (
            "name", "description", "priority",
            "conditions", "match_actions", "no_match_actions",
        ):
            if key in override_data:
                tmpl[key] = override_data[key]

    row = await repo.insert_policy(
        db,
        tenant_id=actor["tenant_id"],
        created_by=actor["sub"],
        name=tmpl["name"],
        description=tmpl["description"],
        priority=tmpl["priority"],
        conditions_json=json.dumps(tmpl["conditions"]),
        match_actions_json=json.dumps(tmpl["match_actions"]),
        no_match_actions_json=json.dumps(tmpl.get("no_match_actions", [])),
        enabled=True,
    )
    return dict(row)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

async def simulate_policy(
    db: AsyncSession,
    *,
    tenant_id: str,
    policy_id: UUID,
    device_context: dict,
) -> dict[str, Any]:
    policy = await repo.lookup_policy(
        db, tenant_id=tenant_id, policy_id=policy_id,
    )
    if not policy:
        raise NotFoundError("Policy", str(policy_id))

    evaluator = PolicyEvaluator()
    eval_result = evaluator.evaluate_with_details(dict(policy), device_context)
    actions = (
        policy["match_actions"] if eval_result["matched"]
        else policy["no_match_actions"]
    )
    return {
        "evaluation": eval_result,
        "would_execute_actions": actions,
    }


async def simulate_all_policies(
    db: AsyncSession,
    *,
    tenant_id: str,
    device_context: dict,
) -> dict[str, Any]:
    policies = await repo.list_enabled_policies(db, tenant_id=tenant_id)

    evaluator = PolicyEvaluator()
    results = []
    winning_policy = None
    for policy in policies:
        eval_result = evaluator.evaluate_with_details(dict(policy), device_context)
        results.append(eval_result)
        if eval_result["matched"] and winning_policy is None:
            winning_policy = {
                "policy_id": str(policy["id"]),
                "policy_name": policy["name"],
                "priority": policy["priority"],
                "actions": policy["match_actions"],
            }
    return {
        "device_context": device_context,
        "winning_policy": winning_policy,
        "all_evaluations": results,
        "total_policies": len(policies),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dump_models(items: list) -> str:
    """JSON-encode a list of pydantic models or plain dicts."""
    return json.dumps(
        [it.model_dump() if hasattr(it, "model_dump") else it for it in items]
    )
