"""Unit tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from orw_common.models.device import DeviceCreate, DeviceUpdate
from orw_common.models.policy import PolicyCreate, PolicyCondition, PolicyAction
from orw_common.models.auth import LoginRequest, UserCreate


class TestDeviceModels:
    def test_device_create_valid(self):
        device = DeviceCreate(mac_address="00:11:22:33:44:55")
        assert device.mac_address == "00:11:22:33:44:55"

    def test_device_create_invalid_mac(self):
        with pytest.raises(ValidationError):
            DeviceCreate(mac_address="invalid-mac")

    def test_device_create_with_optional_fields(self):
        device = DeviceCreate(
            mac_address="AA:BB:CC:DD:EE:FF",
            ip_address="192.168.1.1",
            hostname="test-pc",
            device_type="workstation",
        )
        assert device.hostname == "test-pc"

    def test_device_update_partial(self):
        update = DeviceUpdate(hostname="new-name")
        assert update.hostname == "new-name"
        assert update.ip_address is None

    def test_device_update_risk_score_bounds(self):
        update = DeviceUpdate(risk_score=50)
        assert update.risk_score == 50

        with pytest.raises(ValidationError):
            DeviceUpdate(risk_score=101)

        with pytest.raises(ValidationError):
            DeviceUpdate(risk_score=-1)


class TestPolicyModels:
    def test_policy_create(self):
        policy = PolicyCreate(
            name="Test Policy",
            conditions=[
                PolicyCondition(field="status", operator="equals", value="discovered")
            ],
            match_actions=[
                PolicyAction(type="vlan_assign", params={"vlan_id": 99})
            ],
        )
        assert policy.name == "Test Policy"
        assert len(policy.conditions) == 1

    def test_policy_create_priority_bounds(self):
        with pytest.raises(ValidationError):
            PolicyCreate(
                name="Test",
                priority=0,
                conditions=[],
                match_actions=[],
            )

    def test_policy_create_empty_name(self):
        with pytest.raises(ValidationError):
            PolicyCreate(
                name="",
                conditions=[],
                match_actions=[],
            )


class TestAuthModels:
    def test_login_request(self):
        req = LoginRequest(username="admin", password="password123")
        assert req.username == "admin"

    def test_user_create_valid(self):
        user = UserCreate(
            username="newuser",
            email="user@test.com",
            password="securepass123",
            role="operator",
        )
        assert user.role == "operator"

    def test_user_create_short_username(self):
        with pytest.raises(ValidationError):
            UserCreate(username="ab", password="securepass123")

    def test_user_create_short_password(self):
        with pytest.raises(ValidationError):
            UserCreate(username="validuser", password="short")

    def test_user_create_invalid_role(self):
        with pytest.raises(ValidationError):
            UserCreate(
                username="validuser",
                password="securepass123",
                role="superadmin",
            )
