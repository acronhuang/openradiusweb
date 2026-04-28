"""Pure unit tests for the radius_realms service layer.

Coverage focus is on the validation matrix (proxy completeness, FK refs,
fallback delete-protection) and secret-masking in audit.
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

from features.radius_realms import service
from features.radius_realms import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4()), "username": "alice"}


def _local_realm_fields():
    """Minimal create payload for a `local` realm."""
    return {
        "name": "corp", "description": None,
        "realm_type": "local", "strip_username": True,
        "proxy_host": None, "proxy_port": 1812, "proxy_secret": None,
        "proxy_nostrip": False, "proxy_retry_count": 3,
        "proxy_retry_delay_seconds": 5, "proxy_dead_time_seconds": 120,
        "ldap_server_id": None,
        "auth_types_allowed": ["EAP-TLS", "PEAP", "EAP-TTLS", "MAB"],
        "default_vlan": None, "default_filter_id": None,
        "fallback_realm_id": None,
        "priority": 100, "enabled": True,
    }


def _proxy_realm_fields():
    fields = _local_realm_fields()
    fields.update({
        "name": "upstream", "realm_type": "proxy",
        "proxy_host": "radius.upstream.com",
        "proxy_secret": "topsecret",
    })
    return fields


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_pagination_math_and_filters(self, mock_db, actor):
        with patch.object(repo, "count_realms", AsyncMock(return_value=11)) as cnt, \
             patch.object(repo, "list_realms", AsyncMock(return_value=[])) as lst:
            await service.list_realms(
                mock_db, tenant_id=actor["tenant_id"],
                realm_type="proxy", enabled=True,
                page=2, page_size=5,
            )
        assert cnt.await_args.kwargs["realm_type"] == "proxy"
        assert lst.await_args.kwargs["offset"] == 5


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_realm", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_realm(
                    mock_db, tenant_id=actor["tenant_id"], realm_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# Create — validation matrix
# ---------------------------------------------------------------------------

class TestCreateValidation:
    @pytest.mark.asyncio
    async def test_proxy_without_host_raises_validation(self, mock_db, actor):
        fields = _proxy_realm_fields()
        fields["proxy_host"] = None
        with pytest.raises(ValidationError, match="proxy_host"):
            await service.create_realm(
                mock_db, actor, fields=fields, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_proxy_without_secret_raises_validation(self, mock_db, actor):
        fields = _proxy_realm_fields()
        fields["proxy_secret"] = None
        with pytest.raises(ValidationError, match="proxy_secret"):
            await service.create_realm(
                mock_db, actor, fields=fields, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_unknown_ldap_server_raises_validation(self, mock_db, actor):
        fields = _local_realm_fields()
        fields["ldap_server_id"] = str(uuid4())
        with patch.object(repo, "ldap_server_exists", AsyncMock(return_value=False)):
            with pytest.raises(ValidationError, match="LDAP server"):
                await service.create_realm(
                    mock_db, actor, fields=fields, client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_unknown_fallback_realm_raises_validation(self, mock_db, actor):
        fields = _local_realm_fields()
        fields["fallback_realm_id"] = str(uuid4())
        with patch.object(repo, "realm_exists", AsyncMock(return_value=False)):
            with pytest.raises(ValidationError, match="fallback realm"):
                await service.create_realm(
                    mock_db, actor, fields=fields, client_ip=None,
                )


class TestCreateHappyPath:
    @pytest.mark.asyncio
    async def test_local_realm_inserts_and_publishes_nats(self, mock_db, actor):
        row = {"id": uuid4(), "name": "corp", "realm_type": "local"}
        with patch.object(repo, "insert_realm", AsyncMock(return_value=row)), \
             patch("features.radius_realms.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.radius_realms.service.log_audit",
                   AsyncMock()) as audit:
            await service.create_realm(
                mock_db, actor, fields=_local_realm_fields(), client_ip="1.2.3.4",
            )
        subject, payload = pub.await_args.args
        assert subject == "orw.config.freeradius.apply"
        assert payload["reason"] == "realm_created"
        assert payload["realm_name"] == "corp"
        audit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Update — validation falls back to existing values
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.asyncio
    async def test_no_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_realm(
                mock_db, actor, realm_id=uuid4(), updates={}, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_become_proxy_uses_existing_host_and_secret(self, mock_db, actor):
        # Existing row already has host + secret; update only flips realm_type
        existing = {
            "proxy_host": "old.upstream.com",
            "proxy_secret_encrypted": "existing-secret",
        }
        new_row = {"id": uuid4(), "name": "corp", "realm_type": "proxy"}
        with patch.object(
            repo, "lookup_proxy_state", AsyncMock(return_value=existing),
        ), patch.object(repo, "update_realm", AsyncMock(return_value=new_row)), \
             patch("features.radius_realms.events.nats_client.publish", AsyncMock()), \
             patch("features.radius_realms.service.log_audit", AsyncMock()):
            out = await service.update_realm(
                mock_db, actor,
                realm_id=uuid4(),
                updates={"realm_type": "proxy"},  # no host/secret in update
                client_ip=None,
            )
        assert out["realm_type"] == "proxy"

    @pytest.mark.asyncio
    async def test_become_proxy_with_no_existing_host_raises(self, mock_db, actor):
        existing = {"proxy_host": None, "proxy_secret_encrypted": None}
        with patch.object(
            repo, "lookup_proxy_state", AsyncMock(return_value=existing),
        ):
            with pytest.raises(ValidationError, match="proxy_host"):
                await service.update_realm(
                    mock_db, actor,
                    realm_id=uuid4(),
                    updates={"realm_type": "proxy"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_become_proxy_existing_realm_missing_raises_not_found(
        self, mock_db, actor,
    ):
        with patch.object(repo, "lookup_proxy_state", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_realm(
                    mock_db, actor,
                    realm_id=uuid4(),
                    updates={"realm_type": "proxy"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_proxy_secret_value_never_in_audit(self, mock_db, actor):
        row = {"id": uuid4(), "name": "corp", "realm_type": "local"}
        with patch.object(repo, "update_realm", AsyncMock(return_value=row)), \
             patch("features.radius_realms.events.nats_client.publish", AsyncMock()), \
             patch("features.radius_realms.service.log_audit",
                   AsyncMock()) as audit:
            await service.update_realm(
                mock_db, actor,
                realm_id=uuid4(),
                updates={"proxy_secret": "ultra-secret"},
                client_ip=None,
            )
        details = audit.await_args.kwargs["details"]
        assert sorted(details["changed_fields"]) == ["proxy_secret"]
        assert "ultra-secret" not in str(details)


# ---------------------------------------------------------------------------
# Delete — fallback-reference protection
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_referenced_as_fallback_raises_conflict(self, mock_db, actor):
        with patch.object(
            repo, "count_fallback_references", AsyncMock(return_value=2),
        ):
            with pytest.raises(ConflictError, match="2 other realm"):
                await service.delete_realm(
                    mock_db, actor, realm_id=uuid4(), client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(
            repo, "count_fallback_references", AsyncMock(return_value=0),
        ), patch.object(
            repo, "lookup_realm_summary", AsyncMock(return_value=None),
        ):
            with pytest.raises(NotFoundError):
                await service.delete_realm(
                    mock_db, actor, realm_id=uuid4(), client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_clean_delete_publishes_and_audits(self, mock_db, actor):
        existing = {"id": uuid4(), "name": "corp", "realm_type": "local"}
        with patch.object(
            repo, "count_fallback_references", AsyncMock(return_value=0),
        ), patch.object(
            repo, "lookup_realm_summary", AsyncMock(return_value=existing),
        ), patch.object(repo, "delete_realm", AsyncMock()) as dlt, \
             patch("features.radius_realms.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.radius_realms.service.log_audit", AsyncMock()):
            await service.delete_realm(
                mock_db, actor, realm_id=uuid4(), client_ip=None,
            )
        dlt.assert_awaited_once()
        _, payload = pub.await_args.args
        assert payload["reason"] == "realm_deleted"
