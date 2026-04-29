"""Pure unit tests for the certificates service layer.

Crypto + filesystem are stubbed; we verify orchestration + gates:
  - NotFoundError on missing cert_id
  - ValidationError on missing CA / deleting active cert
  - NATS publish on activate; NOT published on activate-not-found
  - Delete refuses active certs (no DB delete called)
  - Download assembly logic (chain / key inclusion)
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from features.certificates import service
from features.certificates import repository as repo
from orw_common.exceptions import NotFoundError, ValidationError


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


# ---------------------------------------------------------------------------
# get_cert_detail
# ---------------------------------------------------------------------------

class TestGetCertDetail:
    @pytest.mark.asyncio
    async def test_raises_not_found(self, actor):
        with patch.object(repo, "lookup_cert_full", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_cert_detail(
                    AsyncMock(),
                    tenant_id=actor["tenant_id"], cert_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# generate_server — missing CA gate
# ---------------------------------------------------------------------------

class TestGenerateServer:
    @pytest.mark.asyncio
    async def test_no_active_ca_raises_validation(self, actor):
        from orw_common.models.certificate import GenerateServerRequest
        req = GenerateServerRequest(
            name="srv", common_name="auth.orw.local",
            san_dns=[], san_ips=[],
            validity_days=90, key_size=2048,
        )
        with patch.object(repo, "lookup_active_ca", AsyncMock(return_value=None)):
            with pytest.raises(ValidationError):
                await service.generate_server(AsyncMock(), actor, req=req)


# ---------------------------------------------------------------------------
# activate_cert
# ---------------------------------------------------------------------------

class TestActivateCert:
    @pytest.mark.asyncio
    async def test_missing_cert_raises_not_found_no_publish(self, actor):
        with patch.object(repo, "lookup_cert_summary",
                          AsyncMock(return_value=None)), \
             patch("features.certificates.events.publish_freeradius_apply_for_cert",
                   AsyncMock()) as pub:
            with pytest.raises(NotFoundError):
                await service.activate_cert(
                    AsyncMock(), actor, cert_id=uuid4(),
                )
        pub.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_deactivates_others_and_publishes(self, actor):
        cid = uuid4()
        summary = {
            "id": cid, "cert_type": "server",
            "name": "srv", "is_active": False,
        }
        updated = {
            "id": cid, "cert_type": "server", "name": "srv",
            "is_active": True,
            "not_after": datetime(2030, 1, 1, tzinfo=timezone.utc),
        }
        with patch.object(repo, "lookup_cert_summary",
                          AsyncMock(return_value=summary)), \
             patch.object(repo, "deactivate_certs_of_type",
                          AsyncMock()) as deact, \
             patch.object(repo, "set_cert_active",
                          AsyncMock(return_value=updated)), \
             patch("features.certificates.service.log_audit", AsyncMock()), \
             patch("features.certificates.events.publish_freeradius_apply_for_cert",
                   AsyncMock()) as pub:
            out = await service.activate_cert(AsyncMock(), actor, cert_id=cid)
        deact.assert_awaited_once()
        assert deact.await_args.kwargs["cert_type"] == "server"
        pub.assert_awaited_once_with(cert_id=str(cid), cert_type="server")
        assert out["status"] == "valid"  # >30 days


# ---------------------------------------------------------------------------
# delete_cert
# ---------------------------------------------------------------------------

class TestDeleteCert:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, actor):
        with patch.object(repo, "lookup_cert_summary",
                          AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.delete_cert(
                    AsyncMock(), actor, cert_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_active_cert_refused(self, actor):
        summary = {
            "id": uuid4(), "cert_type": "ca",
            "name": "ca-1", "is_active": True,
        }
        with patch.object(repo, "lookup_cert_summary",
                          AsyncMock(return_value=summary)), \
             patch.object(repo, "delete_cert", AsyncMock()) as deleter:
            with pytest.raises(ValidationError):
                await service.delete_cert(
                    AsyncMock(), actor, cert_id=summary["id"],
                )
        deleter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inactive_cert_deleted_and_audited(self, actor):
        summary = {
            "id": uuid4(), "cert_type": "server",
            "name": "old-srv", "is_active": False,
        }
        with patch.object(repo, "lookup_cert_summary",
                          AsyncMock(return_value=summary)), \
             patch.object(repo, "delete_cert", AsyncMock()) as deleter, \
             patch("features.certificates.service.log_audit",
                   AsyncMock()) as audit:
            await service.delete_cert(
                AsyncMock(), actor, cert_id=summary["id"],
            )
        deleter.assert_awaited_once()
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "delete"


# ---------------------------------------------------------------------------
# download_cert
# ---------------------------------------------------------------------------

class TestDownloadCert:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, actor):
        with patch.object(repo, "lookup_cert_for_download",
                          AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.download_cert(
                    AsyncMock(),
                    tenant_id=actor["tenant_id"], cert_id=uuid4(),
                    include_key=False, include_chain=False,
                )

    @pytest.mark.asyncio
    async def test_assembles_with_chain_and_key(self, actor):
        row = {
            "name": "Test Cert",
            "cert_type": "server",
            "pem_data": "PEM-CERT",
            "key_pem_encrypted": "PEM-KEY",
            "chain_pem": "PEM-CHAIN",
        }
        with patch.object(repo, "lookup_cert_for_download",
                          AsyncMock(return_value=row)):
            out = await service.download_cert(
                AsyncMock(),
                tenant_id=actor["tenant_id"], cert_id=uuid4(),
                include_key=True, include_chain=True,
            )
        assert out["pem"] == "PEM-CERT\nPEM-CHAIN\nPEM-KEY"
        assert out["filename"] == "test_cert.pem"

    @pytest.mark.asyncio
    async def test_omits_chain_and_key_by_default(self, actor):
        row = {
            "name": "x", "cert_type": "ca",
            "pem_data": "P", "key_pem_encrypted": "K", "chain_pem": "C",
        }
        with patch.object(repo, "lookup_cert_for_download",
                          AsyncMock(return_value=row)):
            out = await service.download_cert(
                AsyncMock(),
                tenant_id=actor["tenant_id"], cert_id=uuid4(),
                include_key=False, include_chain=False,
            )
        assert out["pem"] == "P"

    @pytest.mark.asyncio
    async def test_no_pem_data_raises_not_found(self, actor):
        row = {
            "name": "x", "cert_type": "ca",
            "pem_data": None, "key_pem_encrypted": None, "chain_pem": None,
        }
        with patch.object(repo, "lookup_cert_for_download",
                          AsyncMock(return_value=row)):
            with pytest.raises(NotFoundError):
                await service.download_cert(
                    AsyncMock(),
                    tenant_id=actor["tenant_id"], cert_id=uuid4(),
                    include_key=False, include_chain=False,
                )


# ---------------------------------------------------------------------------
# list_certs — status decoration
# ---------------------------------------------------------------------------

class TestListCerts:
    @pytest.mark.asyncio
    async def test_decorates_each_row_with_status(self, actor):
        # Use MagicMock so .get("not_after") works on a row-like object
        valid_row = MagicMock()
        valid_row.get.return_value = (
            datetime.now(timezone.utc).replace(year=2099)
        )
        valid_row.__iter__ = lambda self: iter([("not_after", valid_row.get())])
        # Simpler: dict
        rows = [
            {"id": uuid4(), "not_after": datetime(2099, 1, 1, tzinfo=timezone.utc)},
            {"id": uuid4(), "not_after": None},
        ]
        with patch.object(repo, "count_certs", AsyncMock(return_value=2)), \
             patch.object(repo, "list_certs", AsyncMock(return_value=rows)):
            out = await service.list_certs(
                AsyncMock(),
                tenant_id=actor["tenant_id"],
                cert_type=None, enabled=None,
                page=1, page_size=10,
            )
        assert out["total"] == 2
        assert out["items"][0]["status"] == "valid"
        assert out["items"][1]["status"] == "unknown"
