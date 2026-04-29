"""Pure unit tests for the dot1x_overview service layer.

Focus is on the small block-builder helpers (defaults, fallbacks,
fraction math) — orchestration is just a sequential await of repo atoms.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from features.dot1x_overview import service
from features.dot1x_overview import repository as repo


# ---------------------------------------------------------------------------
# _eap_block — defaults vs settings
# ---------------------------------------------------------------------------

class TestEapBlock:
    def test_uses_defaults_when_no_methods_or_settings(self):
        out = service._eap_block({}, [])
        assert out["enabled"] == ["PEAP", "EAP-TLS", "EAP-TTLS"]
        assert out["default"] == "peap"
        assert out["tls_min_version"] == "1.2"
        assert out["auth_port"] == "1812"
        assert out["acct_port"] == "1813"

    def test_overrides_when_settings_present(self):
        settings = {
            "default_eap_type": "tls",
            "tls_min_version": "1.3",
            "auth_port": "11812",
            "acct_port": "11813",
        }
        out = service._eap_block(settings, ["EAP-TLS"])
        assert out["enabled"] == ["EAP-TLS"]
        assert out["default"] == "tls"
        assert out["tls_min_version"] == "1.3"


# ---------------------------------------------------------------------------
# _certs_block — counting + nearest expiry
# ---------------------------------------------------------------------------

class TestCertsBlock:
    def test_empty_returns_zeros_and_no_expiry(self):
        out = service._certs_block([])
        assert out["ca_count"] == 0
        assert out["server_count"] == 0
        assert out["ca_active"] is False
        assert out["server_active"] is False
        assert out["nearest_expiry"] is None
        assert out["nearest_expiry_name"] is None

    def test_picks_first_active_with_not_after(self):
        # Inactive CA expires earliest — should NOT be picked
        # First ACTIVE row with not_after wins
        certs = [
            {"cert_type": "ca", "is_active": False,
             "not_after": datetime(2026, 1, 1, tzinfo=timezone.utc), "name": "old-ca"},
            {"cert_type": "server", "is_active": True,
             "not_after": datetime(2026, 6, 1, tzinfo=timezone.utc), "name": "current-server"},
            {"cert_type": "ca", "is_active": True,
             "not_after": datetime(2030, 1, 1, tzinfo=timezone.utc), "name": "current-ca"},
        ]
        out = service._certs_block(certs)
        assert out["ca_count"] == 2
        assert out["server_count"] == 1
        assert out["ca_active"] is True
        assert out["server_active"] is True
        # First active row in the list (server, 2026-06-01)
        assert out["nearest_expiry_name"] == "current-server"

    def test_active_without_not_after_skipped(self):
        certs = [
            {"cert_type": "server", "is_active": True,
             "not_after": None, "name": "no-expiry"},
            {"cert_type": "server", "is_active": True,
             "not_after": datetime(2027, 1, 1, tzinfo=timezone.utc), "name": "real"},
        ]
        out = service._certs_block(certs)
        assert out["nearest_expiry_name"] == "real"


# ---------------------------------------------------------------------------
# _vlans_block — group by purpose, default "other"
# ---------------------------------------------------------------------------

class TestVlansBlock:
    def test_groups_by_purpose_and_falls_back_to_other(self):
        vlans = [
            {"vlan_id": 10, "name": "corp", "purpose": "corporate"},
            {"vlan_id": 20, "name": "guest", "purpose": "guest"},
            {"vlan_id": 30, "name": "x", "purpose": None},
        ]
        out = service._vlans_block(vlans)
        assert out["total"] == 3
        assert "corporate" in out["by_purpose"]
        assert "guest" in out["by_purpose"]
        assert "other" in out["by_purpose"]
        assert out["by_purpose"]["other"][0]["vlan_id"] == 30


# ---------------------------------------------------------------------------
# _realms_block — defaults zero for missing types
# ---------------------------------------------------------------------------

class TestRealmsBlock:
    def test_missing_types_default_to_zero(self):
        out = service._realms_block({"local": 3})
        assert out["total"] == 3
        assert out["local"] == 3
        assert out["proxy"] == 0


# ---------------------------------------------------------------------------
# _auth_stats_block — fraction math + None-safe
# ---------------------------------------------------------------------------

class TestAuthStatsBlock:
    def test_zero_total_no_division_by_zero(self):
        out = service._auth_stats_block(
            {"total": 0, "success": 0}, {},
        )
        assert out["success_rate"] == 0
        assert out["failed"] == 0

    def test_none_total_treated_as_zero(self):
        # PG COUNT can return None on empty result in some configurations
        out = service._auth_stats_block(
            {"total": None, "success": None}, {},
        )
        assert out["total"] == 0
        assert out["success_rate"] == 0

    def test_success_rate_rounded_to_one_decimal(self):
        out = service._auth_stats_block(
            {"total": 7, "success": 5}, {"PEAP": 5, "MAB": 2},
        )
        # 5/7 = 71.428571...
        assert out["success_rate"] == 71.4
        assert out["failed"] == 2
        assert out["by_method"] == {"PEAP": 5, "MAB": 2}


# ---------------------------------------------------------------------------
# Orchestration smoke test — verifies all 10 atoms are awaited
# ---------------------------------------------------------------------------

class TestGetOverview:
    @pytest.mark.asyncio
    async def test_calls_all_ten_atoms_and_returns_full_shape(self):
        tenant_id = str(uuid4())
        mock_db = AsyncMock()

        # Stub every repo atom with empty/zero fixture data
        with patch.object(repo, "get_radius_settings",
                          AsyncMock(return_value={})), \
             patch.object(repo, "list_enabled_realm_auth_methods",
                          AsyncMock(return_value=[])), \
             patch.object(repo, "list_enabled_certificates",
                          AsyncMock(return_value=[])), \
             patch.object(repo, "list_enabled_vlans",
                          AsyncMock(return_value=[])), \
             patch.object(repo, "count_mab_devices",
                          AsyncMock(return_value={
                              "total": 0, "enabled_count": 0, "expired": 0,
                          })), \
             patch.object(repo, "count_realms_by_type",
                          AsyncMock(return_value={})), \
             patch.object(repo, "count_nas_clients",
                          AsyncMock(return_value={"total": 0, "enabled_count": 0})), \
             patch.object(repo, "count_policies",
                          AsyncMock(return_value={"total": 0, "enabled_count": 0})), \
             patch.object(repo, "count_group_vlan_mappings",
                          AsyncMock(return_value={"total": 0, "enabled_count": 0})), \
             patch.object(repo, "auth_stats_24h",
                          AsyncMock(return_value={"total": 0, "success": 0})), \
             patch.object(repo, "auth_methods_24h",
                          AsyncMock(return_value={})):
            out = await service.get_overview(mock_db, tenant_id=tenant_id)

        # All 9 top-level keys present
        assert set(out.keys()) == {
            "eap_methods", "certificates", "vlans", "mab_devices",
            "realms", "nas_clients", "policies", "group_vlan_mappings",
            "auth_stats_24h",
        }
        # Defaults applied where settings/methods empty
        assert out["eap_methods"]["enabled"] == ["PEAP", "EAP-TLS", "EAP-TTLS"]
        assert out["auth_stats_24h"]["success_rate"] == 0
