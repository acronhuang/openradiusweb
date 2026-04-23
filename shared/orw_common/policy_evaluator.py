"""
OpenRadiusWeb Policy Engine - Core Evaluator (Shared Library)

Moved to shared lib so both gateway and policy_engine can import
without sys.path hacks.
"""

import re
from datetime import datetime, timezone
from typing import Any

from orw_common.logging import get_logger

log = get_logger("evaluator")


class PolicyEvaluator:
    """Evaluate policy conditions against device/session context."""

    def evaluate(self, policy: dict, context: dict) -> bool:
        """
        Evaluate all conditions in a policy against a device/session context.
        All conditions must match (AND logic).
        """
        conditions = policy.get("conditions", [])
        if not conditions:
            return True

        for condition in conditions:
            if isinstance(condition, dict):
                field = condition.get("field", "")
                operator = condition.get("operator", "equals")
                expected = condition.get("value")
                actual = self._resolve_field(field, context)
                if not self._compare(actual, operator, expected):
                    return False
        return True

    def evaluate_with_details(self, policy: dict, context: dict) -> dict:
        """Evaluate policy and return detailed per-condition results."""
        conditions = policy.get("conditions", [])
        results = []
        all_matched = True

        for condition in conditions:
            if isinstance(condition, dict):
                field = condition.get("field", "")
                operator = condition.get("operator", "equals")
                expected = condition.get("value")
                actual = self._resolve_field(field, context)
                matched = self._compare(actual, operator, expected)
                if not matched:
                    all_matched = False
                results.append({
                    "field": field,
                    "operator": operator,
                    "expected": expected,
                    "actual": actual,
                    "matched": matched,
                })

        return {
            "policy_id": policy.get("id"),
            "policy_name": policy.get("name"),
            "matched": all_matched,
            "conditions": results,
        }

    def _resolve_field(self, field: str, context: dict) -> Any:
        """Resolve a dotted field path to a value from the context."""
        if not field:
            return None

        aliases = {
            "device.type": "device_type",
            "device.os": "os_family",
            "device.os_version": "os_version",
            "device.vendor": "vendor",
            "device.mac": "mac_address",
            "device.ip": "ip_address",
            "device.hostname": "hostname",
            "device.status": "status",
            "device.risk_score": "risk_score",
            "auth.method": "auth_method",
            "auth.username": "username",
            "auth.802_1x": "is_dot1x",
            "auth.mab": "is_mab",
            "network.nas_ip": "nas_ip",
            "network.nas_port": "nas_port",
            "network.switch_ip": "nas_ip",
            "network.port": "nas_port_id",
            "network.vlan": "current_vlan",
            "network.ssid": "ssid",
            "user.name": "username",
            "user.domain": "user_domain",
            "user.group": "ad_groups",
            "user.department": "ad_department",
            "user.email": "user_email",
        }

        direct_fields = {
            "mac_address", "ip_address", "hostname", "device_type",
            "os_family", "os_version", "vendor", "model", "status",
            "risk_score", "first_seen", "last_seen",
        }
        if field in direct_fields:
            return context.get(field)

        if field in aliases:
            return context.get(aliases[field])

        underscore_key = field.replace(".", "_")
        if underscore_key in context:
            return context[underscore_key]

        if field.startswith("time."):
            return self._resolve_time_field(field)

        parts = field.split(".")
        properties = context.get("properties", {})
        current = properties
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _resolve_time_field(self, field: str) -> Any:
        """Resolve time-based fields for schedule-based policies."""
        now = datetime.now(timezone.utc)
        time_fields = {
            "time.hour": now.hour,
            "time.minute": now.minute,
            "time.weekday": now.weekday(),
            "time.day_of_month": now.day,
            "time.month": now.month,
            "time.date": now.strftime("%Y-%m-%d"),
            "time.is_business_hours": 9 <= now.hour <= 17 and now.weekday() < 5,
            "time.is_weekend": now.weekday() >= 5,
        }
        return time_fields.get(field)

    def _compare(self, actual: Any, operator: str, expected: Any) -> bool:
        """Evaluate a single condition using the specified operator."""
        actual_n = self._normalize(actual)
        expected_n = self._normalize(expected)

        try:
            match operator:
                case "equals" | "eq" | "==":
                    return self._equals(actual_n, expected_n)
                case "not_equals" | "ne" | "!=":
                    return not self._equals(actual_n, expected_n)
                case "in":
                    if isinstance(expected, list):
                        return actual_n in [self._normalize(v) for v in expected]
                    return str(actual_n) in str(expected_n) if actual_n else False
                case "not_in":
                    if isinstance(expected, list):
                        return actual_n not in [self._normalize(v) for v in expected]
                    return str(actual_n) not in str(expected_n) if actual_n else True
                case "contains":
                    return str(expected_n).lower() in str(actual).lower() if actual else False
                case "not_contains":
                    return str(expected_n).lower() not in str(actual).lower() if actual else True
                case "starts_with":
                    return str(actual).lower().startswith(str(expected_n).lower()) if actual else False
                case "ends_with":
                    return str(actual).lower().endswith(str(expected_n).lower()) if actual else False
                case "gt" | ">":
                    return float(actual or 0) > float(expected or 0)
                case "gte" | ">=":
                    return float(actual or 0) >= float(expected or 0)
                case "lt" | "<":
                    return float(actual or 0) < float(expected or 0)
                case "lte" | "<=":
                    return float(actual or 0) <= float(expected or 0)
                case "between":
                    if isinstance(expected, list) and len(expected) == 2:
                        return float(expected[0]) <= float(actual or 0) <= float(expected[1])
                    return False
                case "regex":
                    return bool(re.search(str(expected), str(actual))) if actual else False
                case "exists":
                    return actual is not None
                case "not_exists":
                    return actual is None
                case "is_empty":
                    return actual is None or actual == "" or actual == []
                case "is_not_empty":
                    return actual is not None and actual != "" and actual != []
                case "is_member_of":
                    if isinstance(actual, list):
                        if isinstance(expected, list):
                            return bool(set(self._normalize(e) for e in expected) & set(self._normalize(a) for a in actual))
                        return expected_n in [self._normalize(a) for a in actual]
                    return False
                case "matches_oui":
                    if actual and expected_n:
                        return str(actual)[:8].lower() == str(expected_n)[:8].lower()
                    return False
                case _:
                    log.warning("unknown_operator", operator=operator)
                    return False

        except (ValueError, TypeError) as e:
            log.debug("condition_eval_error",
                      operator=operator, actual=actual, expected=expected, error=str(e))
            return False

    def _normalize(self, value: Any) -> Any:
        """Normalize a value for comparison."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.lower().strip()
            if v in ("true", "yes", "1"):
                return True
            if v in ("false", "no", "0"):
                return False
            return v
        return value

    def _equals(self, a: Any, b: Any) -> bool:
        """Case-insensitive equality check."""
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if isinstance(a, str) and isinstance(b, str):
            return a.lower() == b.lower()
        if isinstance(a, bool) or isinstance(b, bool):
            return bool(a) == bool(b)
        return a == b


# ============================================================
# Policy Templates - Pre-built NAC Policies
# ============================================================

POLICY_TEMPLATES = {
    "corporate_802.1x": {
        "name": "Corporate 802.1X Access",
        "description": "Authenticated corporate devices -> Corporate VLAN",
        "priority": 100,
        "conditions": [
            {"field": "auth.method", "operator": "in", "value": ["PEAP", "EAP-TLS"]},
            {"field": "user.group", "operator": "is_member_of", "value": ["Domain Users"]},
            {"field": "compliance.antivirus", "operator": "equals", "value": "up_to_date"},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 10, "vlan_name": "Corporate"}},
            {"type": "acl_apply", "params": {"acl": "permit-all"}},
        ],
        "no_match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 99, "vlan_name": "Quarantine"}},
        ],
    },
    "high_risk_quarantine": {
        "name": "High Risk Quarantine",
        "description": "Risk score >= 70 -> auto quarantine",
        "priority": 50,
        "conditions": [
            {"field": "device.risk_score", "operator": "gte", "value": 70},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 99, "vlan_name": "Quarantine"}},
            {"type": "notify", "params": {"template": "high_risk", "channel": "siem"}},
        ],
        "no_match_actions": [],
    },
    "printer_auto": {
        "name": "Printer Auto-Assignment",
        "description": "Identified printers -> Printer VLAN",
        "priority": 200,
        "conditions": [
            {"field": "device.type", "operator": "equals", "value": "printer"},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 40, "vlan_name": "Printer"}},
            {"type": "acl_apply", "params": {"acl": "printer-restricted"}},
        ],
        "no_match_actions": [],
    },
    "ip_phone": {
        "name": "IP Phone VoIP",
        "description": "VoIP phones -> Voice VLAN + QoS",
        "priority": 150,
        "conditions": [
            {"field": "device.type", "operator": "in", "value": ["ip_phone", "voip"]},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 100, "vlan_name": "VoIP"}},
            {"type": "qos_apply", "params": {"dscp": 46}},
        ],
        "no_match_actions": [],
    },
    "iot_isolation": {
        "name": "IoT Device Isolation",
        "description": "IoT/cameras/sensors -> IoT VLAN",
        "priority": 300,
        "conditions": [
            {"field": "device.type", "operator": "in",
             "value": ["iot", "ip_camera", "sensor", "access_point"]},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 30, "vlan_name": "IoT"}},
            {"type": "acl_apply", "params": {"acl": "iot-restricted"}},
        ],
        "no_match_actions": [],
    },
    "guest_byod": {
        "name": "Guest / BYOD Access",
        "description": "MAB-authenticated unknown devices -> Guest VLAN",
        "priority": 500,
        "conditions": [
            {"field": "auth.method", "operator": "equals", "value": "MAB"},
            {"field": "device.type", "operator": "equals", "value": "unknown"},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 20, "vlan_name": "Guest"}},
            {"type": "captive_portal", "params": {"redirect_url": "/guest/register"}},
        ],
        "no_match_actions": [],
    },
    "business_hours": {
        "name": "Business Hours Only",
        "description": "Contractors restricted to business hours",
        "priority": 250,
        "conditions": [
            {"field": "user.group", "operator": "is_member_of", "value": ["Contractors"]},
            {"field": "time.is_business_hours", "operator": "equals", "value": False},
        ],
        "match_actions": [
            {"type": "reject", "params": {"reason": "Access restricted to business hours"}},
            {"type": "notify", "params": {"template": "after_hours", "channel": "siem"}},
        ],
        "no_match_actions": [],
    },
    "non_compliant": {
        "name": "Non-Compliant Remediation",
        "description": "Compliance check failed -> Remediation VLAN",
        "priority": 400,
        "conditions": [
            {"field": "auth.802_1x", "operator": "equals", "value": True},
            {"field": "compliance.overall", "operator": "equals", "value": "fail"},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 98, "vlan_name": "Remediation"}},
            {"type": "captive_portal", "params": {"redirect_url": "/remediation"}},
        ],
        "no_match_actions": [],
    },
    "default_deny": {
        "name": "Default Deny (Catch-all)",
        "description": "No policy matched -> Quarantine",
        "priority": 9999,
        "conditions": [],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 99, "vlan_name": "Quarantine"}},
            {"type": "log", "params": {"level": "warning", "message": "Unmatched device quarantined"}},
        ],
        "no_match_actions": [],
    },
}


# ============================================================
# Action Types Registry
# ============================================================

ACTION_TYPES = {
    "vlan_assign": {
        "description": "Assign device to a specific VLAN",
        "nats_subject": "orw.policy.action.vlan_assign",
    },
    "acl_apply": {
        "description": "Apply Access Control List",
        "nats_subject": "orw.policy.action.acl_apply",
    },
    "quarantine": {
        "description": "Quarantine device",
        "nats_subject": "orw.policy.action.quarantine",
    },
    "reject": {
        "description": "Reject authentication request",
        "nats_subject": "orw.policy.action.reject",
    },
    "coa": {
        "description": "Send RADIUS CoA",
        "nats_subject": "orw.policy.action.coa",
    },
    "bounce_port": {
        "description": "Bounce switch port (shut/no shut)",
        "nats_subject": "orw.switch.bounce_port",
    },
    "captive_portal": {
        "description": "Redirect to captive portal",
        "nats_subject": "orw.policy.action.captive_portal",
    },
    "notify": {
        "description": "Send notification (Email, Slack, SIEM, Webhook)",
        "nats_subject": "orw.policy.action.notify",
    },
    "create_incident": {
        "description": "Create security incident (TheHive / Shuffle SOAR)",
        "nats_subject": "orw.policy.action.create_incident",
    },
    "tag_device": {
        "description": "Tag device",
        "nats_subject": "orw.policy.action.tag_device",
    },
    "qos_apply": {
        "description": "Apply QoS policy (DSCP marking)",
        "nats_subject": "orw.policy.action.qos_apply",
    },
    "log": {
        "description": "Write to audit log",
        "nats_subject": "orw.policy.action.log",
    },
}
