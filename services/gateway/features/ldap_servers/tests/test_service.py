"""Pure unit tests for the ldap_servers service layer."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)

from features.ldap_servers import service
from features.ldap_servers import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4()), "username": "alice"}


def _create_fields():
    """Minimal valid create payload (everything required by the schema)."""
    return {
        "name": "AD-1", "description": None,
        "host": "dc.example.com", "port": 636,
        "use_tls": True, "use_starttls": False,
        "bind_dn": "CN=svc,DC=example,DC=com",
        "bind_password": "secret",
        "base_dn": "DC=example,DC=com",
        "user_search_filter": "(sAMAccountName={0})",
        "user_search_base": None,
        "group_search_filter": None,
        "group_search_base": None,
        "group_membership_attr": "memberOf",
        "username_attr": "sAMAccountName",
        "display_name_attr": "displayName",
        "email_attr": "mail",
        "connect_timeout_seconds": 5,
        "search_timeout_seconds": 10,
        "idle_timeout_seconds": 300,
        "tls_ca_cert": None,
        "tls_require_cert": True,
        "priority": 100,
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_pagination_math(self, mock_db, actor):
        with patch.object(repo, "count_ldap_servers", AsyncMock(return_value=42)), \
             patch.object(repo, "list_ldap_servers", AsyncMock(return_value=[])) as lst:
            out = await service.list_ldap_servers(
                mock_db, tenant_id=actor["tenant_id"],
                enabled=True, page=2, page_size=20,
            )
        assert out["total"] == 42 and out["page"] == 2
        assert lst.await_args.kwargs["offset"] == 20
        assert lst.await_args.kwargs["limit"] == 20


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_ldap_server", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_ldap_server(
                    mock_db, tenant_id=actor["tenant_id"], server_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_inserts_audits_and_publishes_nats(self, mock_db, actor):
        row = {"id": uuid4(), "name": "AD-1", "host": "dc.example.com",
               "port": 636, "enabled": True}
        with patch.object(repo, "insert_ldap_server", AsyncMock(return_value=row)), \
             patch("features.ldap_servers.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.ldap_servers.service.log_audit", AsyncMock()) as audit:
            out = await service.create_ldap_server(
                mock_db, actor,
                fields=_create_fields(),
                client_ip="1.2.3.4",
            )
        # NATS publish carries the right reason
        pub.assert_awaited_once()
        subject, payload = pub.await_args.args
        assert subject == "orw.config.freeradius.apply"
        assert payload["reason"] == "ldap_server_created"
        # Audit
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "create"
        # bind_password not in audit details (only name/host/port)
        assert "bind_password" not in audit.await_args.kwargs["details"]
        assert out["name"] == "AD-1"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.asyncio
    async def test_no_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_ldap_server(
                mock_db, actor, server_id=uuid4(), updates={}, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_no_allowed_columns_raises_validation(self, mock_db, actor):
        with patch.object(
            repo, "update_ldap_server", AsyncMock(side_effect=ValueError("none")),
        ):
            with pytest.raises(ValidationError):
                await service.update_ldap_server(
                    mock_db, actor,
                    server_id=uuid4(),
                    updates={"unknown_field": "value"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "update_ldap_server", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_ldap_server(
                    mock_db, actor,
                    server_id=uuid4(),
                    updates={"name": "new-name"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_audits_changed_field_keys_not_values(self, mock_db, actor):
        row = {"id": uuid4(), "name": "AD-1"}
        with patch.object(repo, "update_ldap_server", AsyncMock(return_value=row)), \
             patch("features.ldap_servers.events.nats_client.publish", AsyncMock()), \
             patch("features.ldap_servers.service.log_audit",
                   AsyncMock()) as audit:
            await service.update_ldap_server(
                mock_db, actor,
                server_id=uuid4(),
                updates={"bind_password": "new-secret", "host": "newhost"},
                client_ip=None,
            )
        details = audit.await_args.kwargs["details"]
        # Field NAMES are recorded; VALUES are not
        assert sorted(details["changed_fields"]) == ["bind_password", "host"]
        assert "new-secret" not in str(details)


# ---------------------------------------------------------------------------
# Delete (with reference check)
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_referenced_by_realms_raises_conflict(self, mock_db, actor):
        with patch.object(repo, "count_realm_references", AsyncMock(return_value=3)):
            with pytest.raises(ConflictError) as exc:
                await service.delete_ldap_server(
                    mock_db, actor, server_id=uuid4(), client_ip=None,
                )
        assert "3 RADIUS realm" in str(exc.value)

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "count_realm_references", AsyncMock(return_value=0)), \
             patch.object(repo, "lookup_ldap_server_summary",
                          AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.delete_ldap_server(
                    mock_db, actor, server_id=uuid4(), client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_clean_delete_publishes_nats_and_audits(self, mock_db, actor):
        existing = {"id": uuid4(), "name": "AD-1"}
        with patch.object(repo, "count_realm_references", AsyncMock(return_value=0)), \
             patch.object(repo, "lookup_ldap_server_summary",
                          AsyncMock(return_value=existing)), \
             patch.object(repo, "delete_ldap_server", AsyncMock()) as dlt, \
             patch("features.ldap_servers.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.ldap_servers.service.log_audit",
                   AsyncMock()) as audit:
            await service.delete_ldap_server(
                mock_db, actor, server_id=uuid4(), client_ip="1.2.3.4",
            )
        dlt.assert_awaited_once()
        # NATS publish with delete reason
        subject, payload = pub.await_args.args
        assert payload["reason"] == "ldap_server_deleted"
        # Audit
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "delete"


# ---------------------------------------------------------------------------
# Test (live LDAP) — service-side helpers
# ---------------------------------------------------------------------------

class TestLookupForTest:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_full_for_test", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.lookup_for_test(
                    mock_db, tenant_id=actor["tenant_id"], server_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_present_returns_full_row_including_password(self, mock_db, actor):
        # The "for test" lookup is the only path that includes the password column
        row = {"id": uuid4(), "host": "dc.example.com",
               "bind_password_encrypted": "secret", "use_tls": False}
        with patch.object(repo, "lookup_full_for_test", AsyncMock(return_value=row)):
            out = await service.lookup_for_test(
                mock_db, tenant_id=actor["tenant_id"], server_id=uuid4(),
            )
        assert "bind_password_encrypted" in out


class TestRecordTestResult:
    @pytest.mark.asyncio
    async def test_calls_repo_and_audit(self, mock_db, actor):
        with patch.object(repo, "update_test_result", AsyncMock()) as upd, \
             patch("features.ldap_servers.service.log_audit",
                   AsyncMock()) as audit:
            await service.record_test_result(
                mock_db, actor,
                server_id=uuid4(), success=True, message="OK",
                audit_details={"success": True},
            )
        upd.assert_awaited_once()
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "test"
