"""Unit tests for the Policy Engine evaluator."""

import sys
import os

# Add service path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/policy_engine"))

from evaluator import PolicyEvaluator


def make_device(**kwargs):
    """Helper to create a device context dict."""
    defaults = {
        "id": "test-device-id",
        "mac_address": "00:11:22:33:44:55",
        "ip_address": "192.168.1.100",
        "hostname": "test-pc",
        "device_type": "workstation",
        "os_family": "windows",
        "os_version": "Windows 10",
        "vendor": "Dell",
        "status": "discovered",
        "risk_score": 0,
        "properties": {},
    }
    defaults.update(kwargs)
    return defaults


class TestPolicyEvaluator:
    def setup_method(self):
        self.evaluator = PolicyEvaluator()

    def test_empty_conditions_matches(self):
        policy = {"conditions": []}
        device = make_device()
        assert self.evaluator.evaluate(policy, device) is True

    def test_equals_match(self):
        policy = {
            "conditions": [
                {"field": "status", "operator": "equals", "value": "discovered"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_equals_no_match(self):
        policy = {
            "conditions": [
                {"field": "status", "operator": "equals", "value": "authenticated"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is False

    def test_not_equals(self):
        policy = {
            "conditions": [
                {"field": "status", "operator": "not_equals", "value": "quarantined"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_in_operator(self):
        policy = {
            "conditions": [
                {"field": "device_type", "operator": "in",
                 "value": ["workstation", "server"]}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_in_operator_no_match(self):
        policy = {
            "conditions": [
                {"field": "device_type", "operator": "in",
                 "value": ["printer", "phone"]}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is False

    def test_contains(self):
        policy = {
            "conditions": [
                {"field": "os_version", "operator": "contains", "value": "Windows"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_gt_operator(self):
        policy = {
            "conditions": [
                {"field": "risk_score", "operator": "gt", "value": 50}
            ]
        }
        device = make_device(risk_score=75)
        assert self.evaluator.evaluate(policy, device) is True

    def test_lt_operator(self):
        policy = {
            "conditions": [
                {"field": "risk_score", "operator": "lt", "value": 50}
            ]
        }
        device = make_device(risk_score=25)
        assert self.evaluator.evaluate(policy, device) is True

    def test_regex_operator(self):
        policy = {
            "conditions": [
                {"field": "hostname", "operator": "regex", "value": r"^test-.*$"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_exists_operator(self):
        policy = {
            "conditions": [
                {"field": "hostname", "operator": "exists", "value": None}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_not_exists_operator(self):
        policy = {
            "conditions": [
                {"field": "hostname", "operator": "not_exists", "value": None}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device(hostname=None)) is True

    def test_multiple_conditions_all_match(self):
        """All conditions must match (AND logic)."""
        policy = {
            "conditions": [
                {"field": "status", "operator": "equals", "value": "authenticated"},
                {"field": "device_type", "operator": "in",
                 "value": ["workstation", "server"]},
                {"field": "risk_score", "operator": "lt", "value": 50},
            ]
        }
        device = make_device(status="authenticated", risk_score=10)
        assert self.evaluator.evaluate(policy, device) is True

    def test_multiple_conditions_one_fails(self):
        policy = {
            "conditions": [
                {"field": "status", "operator": "equals", "value": "authenticated"},
                {"field": "device_type", "operator": "equals", "value": "printer"},
            ]
        }
        device = make_device(status="authenticated")
        assert self.evaluator.evaluate(policy, device) is False

    def test_properties_lookup(self):
        """Test dotted path resolution for device properties."""
        policy = {
            "conditions": [
                {"field": "compliance.antivirus", "operator": "equals",
                 "value": "up_to_date"}
            ]
        }
        device = make_device(
            properties={"compliance": {"antivirus": "up_to_date"}}
        )
        assert self.evaluator.evaluate(policy, device) is True

    def test_device_type_alias(self):
        """Test device.type resolves to device_type."""
        policy = {
            "conditions": [
                {"field": "device.type", "operator": "equals", "value": "workstation"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_case_insensitive_comparison(self):
        policy = {
            "conditions": [
                {"field": "os_family", "operator": "equals", "value": "Windows"}
            ]
        }
        assert self.evaluator.evaluate(policy, make_device()) is True

    def test_boolean_normalization(self):
        """Test that string 'true'/'false' is normalized to bool."""
        policy = {
            "conditions": [
                {"field": "auth.802_1x", "operator": "equals", "value": True}
            ]
        }
        device = make_device(
            properties={"auth": {"802_1x": "true"}}
        )
        assert self.evaluator.evaluate(policy, device) is True
