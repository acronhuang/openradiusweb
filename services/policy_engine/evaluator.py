"""
OpenRadiusWeb Policy Engine - Core Evaluator

Policy evaluation follows a 4-layer model (similar to ClearPass/ISE):

  Layer 1: Service Policy     → Which authentication service to use?
  Layer 2: Authentication     → How to authenticate? (EAP method, auth source)
  Layer 3: Authorization      → What access to grant? (VLAN, ACL, role)
  Layer 4: Enforcement        → Ongoing compliance & re-evaluation

Evaluation order:
  1. Match Service Policy by NAS/port/SSID
  2. Authenticate via configured method (802.1X / MAB / Web Auth)
  3. Evaluate Authorization policies top-down by priority (lower number = higher priority)
  4. First matching policy wins → execute actions
  5. If no policy matches → execute default policy (priority 9999)

Condition operators:
  equals, not_equals, in, not_in, contains, not_contains,
  gt, gte, lt, lte, regex, exists, not_exists,
  starts_with, ends_with, between, is_member_of (AD group)

Field resolution (dotted path):
  "device.type"              → device_type
  "auth.method"              → auth_method from RADIUS
  "auth.802_1x"              → whether 802.1X authenticated
  "user.group"               → AD group membership
  "user.department"          → AD department
  "compliance.antivirus"     → posture check result
  "compliance.patch"         → OS patch level check
  "network.vlan"             → current VLAN
  "network.switch_ip"        → NAS IP
  "network.port"             → switch port
  "time.hour"                → current hour (0-23)
  "time.weekday"             → current weekday (0=Mon, 6=Sun)
  "time.is_business_hours"   → True if Mon-Fri 09:00-17:00
  "properties.<cat>.<key>"   → device_properties EAV lookup
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
        For OR logic, create separate policies with the same actions.
        """
        conditions = policy.get("conditions", [])
        if not conditions:
            return True  # Empty conditions = always match

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
        """
        Evaluate policy and return detailed per-condition results.
        Useful for debugging, policy simulation, and the "Test Policy" UI feature.
        """
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

    # ============================================================
    # Field Resolution
    # ============================================================

    def _resolve_field(self, field: str, context: dict) -> Any:
        """
        Resolve a dotted field path to a value from the context.

        Resolution order:
        1. Direct key match (e.g., "status" → context["status"])
        2. Alias resolution (e.g., "device.type" → context["device_type"])
        3. Properties lookup (e.g., "compliance.antivirus" → context["properties"]["compliance"]["antivirus"])
        4. Time-based fields (e.g., "time.hour" → current hour)
        """
        if not field:
            return None

        # Field aliases for common NAC concepts
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

        # Direct fields
        direct_fields = {
            "mac_address", "ip_address", "hostname", "device_type",
            "os_family", "os_version", "vendor", "model", "status",
            "risk_score", "first_seen", "last_seen",
        }
        if field in direct_fields:
            return context.get(field)

        # Alias resolution
        if field in aliases:
            return context.get(aliases[field])

        # Underscore fallback (device.type → device_type)
        underscore_key = field.replace(".", "_")
        if underscore_key in context:
            return context[underscore_key]

        # Time-based fields
        if field.startswith("time."):
            return self._resolve_time_field(field)

        # Properties lookup (dotted path into nested dict)
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

    # ============================================================
    # Comparison Operators
    # ============================================================

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
                    # AD group membership check
                    if isinstance(actual, list):
                        if isinstance(expected, list):
                            return bool(set(self._normalize(e) for e in expected) & set(self._normalize(a) for a in actual))
                        return expected_n in [self._normalize(a) for a in actual]
                    return False

                case "matches_oui":
                    # MAC OUI prefix match (e.g., "00:50:56" for VMware)
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
    # ──────────────────────────────────────────────
    # 企業 802.1X 認證裝置
    # ──────────────────────────────────────────────
    "corporate_802.1x": {
        "name": "Corporate 802.1X Access",
        "description": "已認證的企業裝置 → Corporate VLAN，需通過合規檢查",
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

    # ──────────────────────────────────────────────
    # 高風險裝置自動隔離
    # ──────────────────────────────────────────────
    "high_risk_quarantine": {
        "name": "High Risk Quarantine",
        "description": "風險分數 ≥ 70 的裝置自動隔離並建立資安事件",
        "priority": 50,
        "conditions": [
            {"field": "device.risk_score", "operator": "gte", "value": 70},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 99, "vlan_name": "Quarantine"}},
            {"type": "notify", "params": {"template": "high_risk", "channel": "siem"}},
            {"type": "create_incident", "params": {
                "title": "High-risk device detected",
                "severity": "high",
                "integration": "thehive",
            }},
        ],
        "no_match_actions": [],
    },

    # ──────────────────────────────────────────────
    # 印表機自動分配
    # ──────────────────────────────────────────────
    "printer_auto": {
        "name": "Printer Auto-Assignment",
        "description": "已辨識的印表機自動分配到 Printer VLAN",
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

    # ──────────────────────────────────────────────
    # IP Phone VoIP VLAN
    # ──────────────────────────────────────────────
    "ip_phone": {
        "name": "IP Phone VoIP",
        "description": "VoIP 電話自動分配 Voice VLAN + QoS",
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

    # ──────────────────────────────────────────────
    # IoT 裝置隔離
    # ──────────────────────────────────────────────
    "iot_isolation": {
        "name": "IoT Device Isolation",
        "description": "IoT/攝影機/感測器 → 隔離 VLAN",
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

    # ──────────────────────────────────────────────
    # 訪客 / BYOD
    # ──────────────────────────────────────────────
    "guest_byod": {
        "name": "Guest / BYOD Access",
        "description": "MAB 認證的未知裝置 → Guest VLAN + Captive Portal",
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

    # ──────────────────────────────────────────────
    # 非上班時間限制
    # ──────────────────────────────────────────────
    "business_hours": {
        "name": "Business Hours Only",
        "description": "約聘人員僅限上班時間存取",
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

    # ──────────────────────────────────────────────
    # 不合規裝置 → 修復 VLAN
    # ──────────────────────────────────────────────
    "non_compliant": {
        "name": "Non-Compliant Remediation",
        "description": "合規檢查失敗 → 修復 VLAN + 自助修復頁面",
        "priority": 400,
        "conditions": [
            {"field": "auth.802_1x", "operator": "equals", "value": True},
            {"field": "compliance.overall", "operator": "equals", "value": "fail"},
        ],
        "match_actions": [
            {"type": "vlan_assign", "params": {"vlan_id": 98, "vlan_name": "Remediation"}},
            {"type": "captive_portal", "params": {
                "redirect_url": "/remediation",
                "message": "您的裝置不符合安全要求，請更新後重新連線。",
            }},
        ],
        "no_match_actions": [],
    },

    # ──────────────────────────────────────────────
    # 預設拒絕 (Catch-all)
    # ──────────────────────────────────────────────
    "default_deny": {
        "name": "Default Deny (Catch-all)",
        "description": "未匹配任何策略的裝置 → 隔離",
        "priority": 9999,
        "conditions": [],  # 空條件 = 永遠匹配
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
        "description": "指派裝置到特定 VLAN",
        "nats_subject": "orw.policy.action.vlan_assign",
        "params_schema": {
            "vlan_id": {"type": "integer", "required": True, "min": 1, "max": 4094},
            "vlan_name": {"type": "string", "required": False},
        },
    },
    "acl_apply": {
        "description": "套用存取控制清單 (ACL)",
        "nats_subject": "orw.policy.action.acl_apply",
        "params_schema": {
            "acl": {"type": "string", "required": True},
        },
    },
    "quarantine": {
        "description": "隔離裝置",
        "nats_subject": "orw.policy.action.quarantine",
        "params_schema": {
            "reason": {"type": "string", "required": False},
            "duration_minutes": {"type": "integer", "required": False},
        },
    },
    "reject": {
        "description": "拒絕認證請求",
        "nats_subject": "orw.policy.action.reject",
        "params_schema": {
            "reason": {"type": "string", "required": False},
        },
    },
    "coa": {
        "description": "發送 RADIUS CoA (Change of Authorization)",
        "nats_subject": "orw.policy.action.coa",
        "params_schema": {
            "action": {"type": "string", "required": True,
                       "enum": ["reauthenticate", "disconnect", "bounce-port"]},
        },
    },
    "bounce_port": {
        "description": "彈跳交換器端口 (shut/no shut)",
        "nats_subject": "orw.switch.bounce_port",
        "params_schema": {},
    },
    "captive_portal": {
        "description": "重導向到 Captive Portal / Web 認證頁",
        "nats_subject": "orw.policy.action.captive_portal",
        "params_schema": {
            "redirect_url": {"type": "string", "required": True},
            "message": {"type": "string", "required": False},
        },
    },
    "notify": {
        "description": "發送通知 (Email, Slack, SIEM, Webhook)",
        "nats_subject": "orw.policy.action.notify",
        "params_schema": {
            "template": {"type": "string", "required": True},
            "channel": {"type": "string", "required": True,
                        "enum": ["email", "slack", "siem", "webhook"]},
            "recipients": {"type": "array", "required": False},
        },
    },
    "create_incident": {
        "description": "建立資安事件 (TheHive / Shuffle SOAR)",
        "nats_subject": "orw.policy.action.create_incident",
        "params_schema": {
            "title": {"type": "string", "required": True},
            "severity": {"type": "string", "required": True,
                         "enum": ["critical", "high", "medium", "low"]},
            "integration": {"type": "string", "required": True,
                            "enum": ["thehive", "shuffle"]},
        },
    },
    "tag_device": {
        "description": "為裝置加上標籤",
        "nats_subject": "orw.policy.action.tag_device",
        "params_schema": {
            "tag": {"type": "string", "required": True},
        },
    },
    "qos_apply": {
        "description": "套用 QoS 政策 (DSCP 標記)",
        "nats_subject": "orw.policy.action.qos_apply",
        "params_schema": {
            "dscp": {"type": "integer", "required": True, "min": 0, "max": 63},
        },
    },
    "log": {
        "description": "寫入稽核日誌",
        "nats_subject": "orw.policy.action.log",
        "params_schema": {
            "level": {"type": "string", "required": False,
                      "enum": ["info", "warning", "error"]},
            "message": {"type": "string", "required": True},
        },
    },
}
