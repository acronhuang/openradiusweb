"""Pure unit tests for the policies service layer."""
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from features.policies import service
from features.policies import repository as repo
from orw_common.exceptions import NotFoundError, ValidationError


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


# ---------------------------------------------------------------------------
# list_policies — pagination shape
# ---------------------------------------------------------------------------

class TestListPolicies:
    @pytest.mark.asyncio
    async def test_returns_total_and_items(self, actor):
        with patch.object(repo, "count_policies", AsyncMock(return_value=42)), \
             patch.object(repo, "list_policies",
                          AsyncMock(return_value=[{"id": "x"}])) as lst:
            out = await service.list_policies(
                AsyncMock(),
                tenant_id=actor["tenant_id"],
                enabled=True, page=2, page_size=20,
            )
        assert out["total"] == 42
        assert out["page"] == 2
        assert out["page_size"] == 20
        assert lst.await_args.kwargs["offset"] == 20
        assert lst.await_args.kwargs["limit"] == 20


# ---------------------------------------------------------------------------
# get_policy — NotFoundError
# ---------------------------------------------------------------------------

class TestGetPolicy:
    @pytest.mark.asyncio
    async def test_raises_not_found(self, actor):
        with patch.object(repo, "lookup_policy", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_policy(
                    AsyncMock(),
                    tenant_id=actor["tenant_id"], policy_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# update_policy — empty + json encoding + missing row
# ---------------------------------------------------------------------------

class TestUpdatePolicy:
    @pytest.mark.asyncio
    async def test_empty_updates_raises_validation(self, actor):
        from orw_common.models.policy import PolicyUpdate
        # All fields unset → exclude_unset returns {}
        with pytest.raises(ValidationError):
            await service.update_policy(
                AsyncMock(), actor,
                policy_id=uuid4(),
                req=PolicyUpdate(),
            )

    @pytest.mark.asyncio
    async def test_json_columns_get_dumped_before_repo(self, actor):
        from orw_common.models.policy import PolicyCondition, PolicyUpdate
        cond = PolicyCondition(
            field="device_type", operator="equals", value="laptop",
        )
        with patch.object(
            repo, "update_policy",
            AsyncMock(return_value={"id": uuid4(), "name": "n", "match_actions": [], "no_match_actions": []}),
        ) as upd, \
             patch("features.policies.events.publish_policy_updated", AsyncMock()), \
             patch("features.policies.service.log_audit", AsyncMock()):
            await service.update_policy(
                AsyncMock(), actor,
                policy_id=uuid4(),
                req=PolicyUpdate(conditions=[cond], priority=99),
            )
        sent = upd.await_args.kwargs["updates"]
        assert sent["priority"] == 99
        assert isinstance(sent["conditions"], str)
        # Round-trips through json
        decoded = json.loads(sent["conditions"])
        assert decoded[0]["field"] == "device_type"

    @pytest.mark.asyncio
    async def test_missing_row_raises_not_found(self, actor):
        from orw_common.models.policy import PolicyUpdate
        with patch.object(repo, "update_policy", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_policy(
                    AsyncMock(), actor,
                    policy_id=uuid4(), req=PolicyUpdate(name="x"),
                )


# ---------------------------------------------------------------------------
# delete_policy — NotFound vs NATS publish
# ---------------------------------------------------------------------------

class TestDeletePolicy:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found_no_publish(self, actor):
        with patch.object(repo, "delete_policy", AsyncMock(return_value=False)), \
             patch("features.policies.events.publish_policy_deleted",
                   AsyncMock()) as pub, \
             patch("features.policies.service.log_audit", AsyncMock()):
            with pytest.raises(NotFoundError):
                await service.delete_policy(
                    AsyncMock(), actor, policy_id=uuid4(),
                )
        pub.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_publishes_and_audits(self, actor):
        pid = uuid4()
        with patch.object(repo, "delete_policy", AsyncMock(return_value=True)), \
             patch("features.policies.events.publish_policy_deleted",
                   AsyncMock()) as pub, \
             patch("features.policies.service.log_audit",
                   AsyncMock()) as audit:
            await service.delete_policy(AsyncMock(), actor, policy_id=pid)
        pub.assert_awaited_once_with(policy_id=str(pid))
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["resource_id"] == str(pid)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class TestListTemplates:
    def test_returns_sorted_by_priority(self):
        out = service.list_templates()
        # Sorted by priority ascending
        priorities = [t["priority"] for t in out["templates"]]
        assert priorities == sorted(priorities)
        assert "action_types" in out


class TestApplyTemplate:
    @pytest.mark.asyncio
    async def test_unknown_template_raises_not_found(self, actor):
        with pytest.raises(NotFoundError):
            await service.apply_template(
                AsyncMock(), actor, template_id="does-not-exist",
            )

    @pytest.mark.asyncio
    async def test_overrides_applied_before_insert(self, actor):
        from orw_common.policy_evaluator import POLICY_TEMPLATES
        from orw_common.models.policy import PolicyTemplateOverrides
        any_tid = next(iter(POLICY_TEMPLATES.keys()))
        with patch.object(
            repo, "insert_policy",
            AsyncMock(return_value={"id": uuid4(), "name": "Custom"}),
        ) as ins:
            await service.apply_template(
                AsyncMock(), actor,
                template_id=any_tid,
                overrides=PolicyTemplateOverrides(name="Custom", priority=5),
            )
        # Override values reach the insert
        assert ins.await_args.kwargs["name"] == "Custom"
        assert ins.await_args.kwargs["priority"] == 5


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class TestSimulatePolicy:
    @pytest.mark.asyncio
    async def test_unknown_policy_raises_not_found(self, actor):
        with patch.object(repo, "lookup_policy", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.simulate_policy(
                    AsyncMock(),
                    tenant_id=actor["tenant_id"],
                    policy_id=uuid4(),
                    device_context={},
                )

    @pytest.mark.asyncio
    async def test_match_returns_match_actions(self, actor):
        policy = {
            "id": uuid4(), "name": "p", "priority": 1,
            "conditions": [], "match_actions": [{"action_type": "allow"}],
            "no_match_actions": [{"action_type": "deny"}],
        }
        with patch.object(repo, "lookup_policy",
                          AsyncMock(return_value=policy)), \
             patch("features.policies.service.PolicyEvaluator") as Evaluator:
            Evaluator.return_value.evaluate_with_details.return_value = {
                "matched": True, "details": [],
            }
            out = await service.simulate_policy(
                AsyncMock(),
                tenant_id=actor["tenant_id"],
                policy_id=policy["id"],
                device_context={"foo": "bar"},
            )
        assert out["would_execute_actions"] == [{"action_type": "allow"}]

    @pytest.mark.asyncio
    async def test_no_match_returns_no_match_actions(self, actor):
        policy = {
            "id": uuid4(), "name": "p", "priority": 1,
            "conditions": [], "match_actions": [{"action_type": "allow"}],
            "no_match_actions": [{"action_type": "deny"}],
        }
        with patch.object(repo, "lookup_policy",
                          AsyncMock(return_value=policy)), \
             patch("features.policies.service.PolicyEvaluator") as Evaluator:
            Evaluator.return_value.evaluate_with_details.return_value = {
                "matched": False, "details": [],
            }
            out = await service.simulate_policy(
                AsyncMock(),
                tenant_id=actor["tenant_id"],
                policy_id=policy["id"],
                device_context={},
            )
        assert out["would_execute_actions"] == [{"action_type": "deny"}]


class TestSimulateAll:
    @pytest.mark.asyncio
    async def test_picks_first_match_as_winner(self, actor):
        p1 = {"id": uuid4(), "name": "low", "priority": 1, "match_actions": [{"a": 1}]}
        p2 = {"id": uuid4(), "name": "med", "priority": 2, "match_actions": [{"a": 2}]}
        p3 = {"id": uuid4(), "name": "hi",  "priority": 3, "match_actions": [{"a": 3}]}
        with patch.object(repo, "list_enabled_policies",
                          AsyncMock(return_value=[p1, p2, p3])), \
             patch("features.policies.service.PolicyEvaluator") as Evaluator:
            # First doesn't match, second does, third does
            Evaluator.return_value.evaluate_with_details.side_effect = [
                {"matched": False}, {"matched": True}, {"matched": True},
            ]
            out = await service.simulate_all_policies(
                AsyncMock(),
                tenant_id=actor["tenant_id"],
                device_context={},
            )
        assert out["total_policies"] == 3
        assert out["winning_policy"]["policy_name"] == "med"
        assert out["winning_policy"]["actions"] == [{"a": 2}]

    @pytest.mark.asyncio
    async def test_no_match_returns_none_winner(self, actor):
        p1 = {"id": uuid4(), "name": "x", "priority": 1, "match_actions": []}
        with patch.object(repo, "list_enabled_policies",
                          AsyncMock(return_value=[p1])), \
             patch("features.policies.service.PolicyEvaluator") as Evaluator:
            Evaluator.return_value.evaluate_with_details.return_value = {
                "matched": False,
            }
            out = await service.simulate_all_policies(
                AsyncMock(),
                tenant_id=actor["tenant_id"],
                device_context={},
            )
        assert out["winning_policy"] is None
