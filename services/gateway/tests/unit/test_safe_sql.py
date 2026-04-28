"""Unit tests for utils/safe_sql.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from utils.safe_sql import (
    build_safe_set_clause,
    DEVICE_UPDATE_COLUMNS,
    USER_UPDATE_COLUMNS,
    POLICY_UPDATE_COLUMNS,
    POLICY_TYPE_CASTS,
    LDAP_SERVER_UPDATE_COLUMNS,
    NAS_CLIENT_UPDATE_COLUMNS,
    REALM_UPDATE_COLUMNS,
)


class TestBuildSafeSetClause:
    """Tests for build_safe_set_clause."""

    def test_basic_update(self):
        """Allowed columns produce correct SET clause."""
        updates = {"hostname": "srv1", "status": "online"}
        clause, params = build_safe_set_clause(updates, DEVICE_UPDATE_COLUMNS)
        assert "hostname = :hostname" in clause
        assert "status = :status" in clause
        assert params["hostname"] == "srv1"
        assert params["status"] == "online"

    def test_unknown_columns_skipped(self):
        """Columns not in allowlist are silently skipped."""
        updates = {"hostname": "srv1", "evil_col": "drop table"}
        clause, params = build_safe_set_clause(updates, DEVICE_UPDATE_COLUMNS)
        assert "evil_col" not in clause
        assert "evil_col" not in params
        assert "hostname = :hostname" in clause

    def test_all_unknown_raises(self):
        """If no valid columns remain, ValueError is raised."""
        updates = {"not_a_column": "val"}
        with pytest.raises(ValueError, match="No valid columns"):
            build_safe_set_clause(updates, DEVICE_UPDATE_COLUMNS)

    def test_empty_updates_raises(self):
        """Empty updates dict raises ValueError."""
        with pytest.raises(ValueError, match="No valid columns"):
            build_safe_set_clause({}, DEVICE_UPDATE_COLUMNS)

    def test_column_map(self):
        """column_map remaps request field names to DB column names."""
        updates = {"bind_password": "secret123"}
        clause, params = build_safe_set_clause(
            updates,
            LDAP_SERVER_UPDATE_COLUMNS,
            column_map={"bind_password": "bind_password_encrypted"},
        )
        assert "bind_password_encrypted = :bind_password_encrypted" in clause
        assert params["bind_password_encrypted"] == "secret123"
        # Only the mapped name should appear as a column assignment
        assert "bind_password =" not in clause

    def test_type_casts(self):
        """type_casts adds PostgreSQL ::type suffix."""
        updates = {"conditions": "[{}]", "name": "test-policy"}
        clause, params = build_safe_set_clause(
            updates, POLICY_UPDATE_COLUMNS, type_casts=POLICY_TYPE_CASTS
        )
        assert "conditions = :conditions::jsonb" in clause
        assert "name = :name" in clause
        # name should NOT have a cast
        assert "name = :name::" not in clause
        assert params["conditions"] == "[{}]"

    def test_multiple_type_casts(self):
        """Multiple JSONB casts are applied correctly."""
        updates = {
            "conditions": "[]",
            "match_actions": "[]",
            "no_match_actions": "[]",
        }
        clause, params = build_safe_set_clause(
            updates, POLICY_UPDATE_COLUMNS, type_casts=POLICY_TYPE_CASTS
        )
        assert "conditions = :conditions::jsonb" in clause
        assert "match_actions = :match_actions::jsonb" in clause
        assert "no_match_actions = :no_match_actions::jsonb" in clause

    def test_sql_injection_in_column_name(self):
        """Malicious column names are rejected."""
        updates = {"hostname; DROP TABLE devices--": "val"}
        with pytest.raises(ValueError):
            build_safe_set_clause(updates, DEVICE_UPDATE_COLUMNS)

    def test_column_map_with_unmapped_field(self):
        """Unmapped fields fall through to identity mapping."""
        updates = {"email": "a@b.com", "role": "admin"}
        clause, params = build_safe_set_clause(
            updates, USER_UPDATE_COLUMNS, column_map={}
        )
        assert "email = :email" in clause
        assert "role = :role" in clause


class TestColumnAllowlists:
    """Verify allowlist sets have expected members."""

    def test_device_columns(self):
        assert "hostname" in DEVICE_UPDATE_COLUMNS
        assert "ip_address" in DEVICE_UPDATE_COLUMNS
        assert "id" not in DEVICE_UPDATE_COLUMNS
        assert "tenant_id" not in DEVICE_UPDATE_COLUMNS

    def test_user_columns(self):
        assert "email" in USER_UPDATE_COLUMNS
        assert "password_hash" not in USER_UPDATE_COLUMNS

    def test_policy_columns(self):
        assert "conditions" in POLICY_UPDATE_COLUMNS
        assert "match_actions" in POLICY_UPDATE_COLUMNS
        assert "created_by" not in POLICY_UPDATE_COLUMNS

    def test_policy_type_casts(self):
        assert POLICY_TYPE_CASTS["conditions"] == "jsonb"
        assert POLICY_TYPE_CASTS["match_actions"] == "jsonb"
        assert "name" not in POLICY_TYPE_CASTS

    def test_nas_client_columns(self):
        # The DB column is `secret_encrypted` (see migrations/002_settings_radius_features.sql).
        # The request field `shared_secret` is mapped to it via column_map at the route layer.
        assert "secret_encrypted" in NAS_CLIENT_UPDATE_COLUMNS
        assert "id" not in NAS_CLIENT_UPDATE_COLUMNS

    def test_realm_columns(self):
        assert "ldap_server_id" in REALM_UPDATE_COLUMNS
        assert "tenant_id" not in REALM_UPDATE_COLUMNS
