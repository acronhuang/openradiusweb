"""Tests for the cert auto-renewal background task.

Two layers:

1. Pure helper logic (`_split_san_dns_ips`, `_renewal_name`) — no
   mocks needed.
2. `auto_renew_expiring_server_certs` orchestration — mocks the repo
   query + the inner `generate_server` / `activate_cert` calls so
   nothing touches a real DB or CA.

The actor lookup in `auto_renewal._resolve_system_actor` hits the DB
and is exercised in integration tests instead (it's a single SELECT).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from features.certificates import service


# ---------------------------------------------------------------------------
# _split_san_dns_ips
# ---------------------------------------------------------------------------

class TestSplitSanDnsIps:
    def test_empty(self):
        assert service._split_san_dns_ips(None) == ([], [])
        assert service._split_san_dns_ips([]) == ([], [])

    def test_dns_only(self):
        dns, ips = service._split_san_dns_ips(["radius.mds.local", "auth.mds.local"])
        assert dns == ["radius.mds.local", "auth.mds.local"]
        assert ips == []

    def test_ipv4_only(self):
        dns, ips = service._split_san_dns_ips(["192.168.0.250", "10.0.0.1"])
        assert dns == []
        assert ips == ["192.168.0.250", "10.0.0.1"]

    def test_ipv6_routes_to_ips(self):
        dns, ips = service._split_san_dns_ips(["fe80::1", "::1"])
        assert dns == []
        assert ips == ["fe80::1", "::1"]

    def test_mixed(self):
        dns, ips = service._split_san_dns_ips(
            ["radius.mds.local", "192.168.0.250", "auth.mds.local", "::1"]
        )
        assert dns == ["radius.mds.local", "auth.mds.local"]
        assert ips == ["192.168.0.250", "::1"]


# ---------------------------------------------------------------------------
# _renewal_name
# ---------------------------------------------------------------------------

class TestRenewalName:
    def test_appends_date_suffix(self):
        when = datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc)
        assert service._renewal_name("radius-server", when) == "radius-server-renewed-20260603"

    def test_truncates_to_fit_255_char_column(self):
        when = datetime(2026, 6, 3, tzinfo=timezone.utc)
        long_name = "a" * 250
        out = service._renewal_name(long_name, when)
        assert len(out) <= 255
        assert out.endswith("-renewed-20260603")

    def test_idempotent_within_same_day(self):
        """Two calls on the same day produce the same name. The
        UNIQUE(name, tenant_id) constraint will catch the second
        renewal attempt — that's the loop's idempotency guard."""
        when = datetime(2026, 6, 3, tzinfo=timezone.utc)
        a = service._renewal_name("srv", when)
        b = service._renewal_name("srv", when)
        assert a == b


# ---------------------------------------------------------------------------
# auto_renew_expiring_server_certs orchestration
# ---------------------------------------------------------------------------

@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


def _expiring_cert_row(name: str, days_until_expiry: int, **overrides):
    """Stand-in for one row coming back from
    list_renewable_server_certs_within."""
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid4(),
        "name": name,
        "common_name": f"{name}.mds.local",
        "subject_alt_names": [f"{name}.mds.local", "192.168.0.250"],
        "key_size": 2048,
        "not_before": now - timedelta(days=730 - days_until_expiry),
        "not_after": now + timedelta(days=days_until_expiry),
    }
    base.update(overrides)
    return base


class TestAutoRenewExpiringServerCerts:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_zero_summary(self, actor):
        with patch(
            "features.certificates.repository.list_renewable_server_certs_within",
            AsyncMock(return_value=[]),
        ):
            summary = await service.auto_renew_expiring_server_certs(
                AsyncMock(), actor, threshold_days=30,
            )
        assert summary == {"checked": 0, "renewed": [], "errors": []}

    @pytest.mark.asyncio
    async def test_single_candidate_renews_and_activates(self, actor):
        old = _expiring_cert_row("radius-server", days_until_expiry=10)
        new_id = uuid4()

        with (
            patch(
                "features.certificates.repository.list_renewable_server_certs_within",
                AsyncMock(return_value=[old]),
            ),
            patch.object(
                service, "generate_server",
                AsyncMock(return_value={"id": new_id, "name": "stub"}),
            ) as gen,
            patch.object(service, "activate_cert", AsyncMock()) as act,
        ):
            summary = await service.auto_renew_expiring_server_certs(
                AsyncMock(), actor, threshold_days=30,
            )

        assert summary["checked"] == 1
        assert len(summary["renewed"]) == 1
        assert summary["renewed"][0].startswith("radius-server-renewed-")
        assert summary["errors"] == []

        # generate_server called with reconstructed request preserving
        # the old cert's CN, SAN, key_size, validity
        gen.assert_awaited_once()
        req = gen.await_args.kwargs["req"]
        assert req.common_name == "radius-server.mds.local"
        assert req.san_dns == ["radius-server.mds.local"]
        assert req.san_ips == ["192.168.0.250"]
        assert req.key_size == 2048
        # validity_days = (not_after - not_before).days = 730 (we built
        # the row with that gap)
        assert req.validity_days == 730

        # activate_cert called on the NEW row's id, not the old one
        act.assert_awaited_once()
        assert act.await_args.kwargs["cert_id"] == new_id

    @pytest.mark.asyncio
    async def test_per_candidate_failure_does_not_block_others(self, actor):
        a = _expiring_cert_row("srv-a", days_until_expiry=5)
        b = _expiring_cert_row("srv-b", days_until_expiry=5)

        async def gen_side_effect(*args, **kwargs):
            if kwargs["req"].name.startswith("srv-a"):
                raise RuntimeError("simulated CA signing failure")
            return {"id": uuid4(), "name": kwargs["req"].name}

        with (
            patch(
                "features.certificates.repository.list_renewable_server_certs_within",
                AsyncMock(return_value=[a, b]),
            ),
            patch.object(service, "generate_server", AsyncMock(side_effect=gen_side_effect)),
            patch.object(service, "activate_cert", AsyncMock()),
        ):
            summary = await service.auto_renew_expiring_server_certs(
                AsyncMock(), actor, threshold_days=30,
            )

        assert summary["checked"] == 2
        # srv-b succeeded
        assert any("srv-b-renewed-" in n for n in summary["renewed"])
        # srv-a failed — recorded but didn't abort the loop
        assert any("srv-a" in e and "simulated CA signing failure" in e for e in summary["errors"])

    @pytest.mark.asyncio
    async def test_falls_back_to_730_validity_when_dates_missing(self, actor):
        """Defensive: a row with NULL not_before (shouldn't happen for
        non-imported rows, but the schema allows it) renews using the
        GenerateServerRequest default validity."""
        broken = _expiring_cert_row(
            "srv-no-dates", days_until_expiry=5,
            not_before=None, not_after=None,
        )

        with (
            patch(
                "features.certificates.repository.list_renewable_server_certs_within",
                AsyncMock(return_value=[broken]),
            ),
            patch.object(
                service, "generate_server",
                AsyncMock(return_value={"id": uuid4(), "name": "stub"}),
            ) as gen,
            patch.object(service, "activate_cert", AsyncMock()),
        ):
            await service.auto_renew_expiring_server_certs(
                AsyncMock(), actor, threshold_days=30,
            )
        assert gen.await_args.kwargs["req"].validity_days == 730
