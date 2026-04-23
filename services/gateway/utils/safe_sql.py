"""Safe SQL utilities to prevent column-name injection in dynamic queries."""


def build_safe_set_clause(
    updates: dict,
    allowed_columns: set[str],
    *,
    column_map: dict[str, str] | None = None,
    type_casts: dict[str, str] | None = None,
) -> tuple[str, dict]:
    """Build a safe SET clause for UPDATE queries.

    Only allows column names present in the allowed_columns set.
    Optionally maps request field names to actual DB column names.

    Args:
        updates: Dict of {field_name: value} from request.
        allowed_columns: Set of permitted DB column names.
        column_map: Optional dict mapping request field names to DB column names.
                    e.g. {"bind_password": "bind_password_encrypted"}
        type_casts: Optional dict of column->type for PostgreSQL type casts.
                    e.g. {"conditions": "jsonb"}  →  conditions = :conditions::jsonb

    Returns:
        Tuple of (set_clause_str, params_dict).
        The set_clause_str does NOT include leading "SET " or trailing ", updated_at = NOW()".

    Raises:
        ValueError: If no valid columns remain after filtering.
    """
    column_map = column_map or {}
    type_casts = type_casts or {}
    set_parts: list[str] = []
    params: dict = {}

    for field, value in updates.items():
        # Resolve actual DB column name
        db_col = column_map.get(field, field)
        if db_col not in allowed_columns:
            continue  # silently skip unknown columns
        param_name = db_col
        cast = type_casts.get(db_col, "")
        cast_suffix = f"::{cast}" if cast else ""
        set_parts.append(f"{db_col} = :{param_name}{cast_suffix}")
        params[param_name] = value

    if not set_parts:
        raise ValueError("No valid columns to update")

    return ", ".join(set_parts), params


# ---------------------------------------------------------------------------
# Column allowlists per table
# ---------------------------------------------------------------------------

DEVICE_UPDATE_COLUMNS = {
    "hostname", "ip_address", "device_type", "os_family",
    "os_version", "vendor", "model", "status", "risk_score",
}

USER_UPDATE_COLUMNS = {
    "email", "role", "enabled",
}

LDAP_SERVER_UPDATE_COLUMNS = {
    "name", "description", "host", "port", "use_tls", "use_starttls",
    "bind_dn", "bind_password_encrypted", "base_dn",
    "user_search_filter", "user_search_base",
    "group_search_filter", "group_search_base", "group_membership_attr",
    "username_attr", "display_name_attr", "email_attr",
    "connect_timeout_seconds", "search_timeout_seconds", "idle_timeout_seconds",
    "tls_ca_cert", "tls_require_cert", "priority", "enabled",
}

NAS_CLIENT_UPDATE_COLUMNS = {
    "name", "ip_address", "secret_encrypted",
    "shortname", "nas_type", "description", "enabled",
}

POLICY_UPDATE_COLUMNS = {
    "name", "description", "priority", "conditions",
    "match_actions", "no_match_actions", "enabled",
}

POLICY_TYPE_CASTS = {
    "conditions": "jsonb",
    "match_actions": "jsonb",
    "no_match_actions": "jsonb",
}

REALM_UPDATE_COLUMNS = {
    "name", "description", "realm_type", "strip_username",
    "proxy_host", "proxy_port", "proxy_secret_encrypted", "proxy_nostrip",
    "proxy_retry_count", "proxy_retry_delay_seconds", "proxy_dead_time_seconds",
    "ldap_server_id", "auth_types_allowed",
    "default_vlan", "default_filter_id", "fallback_realm_id",
    "priority", "enabled",
}

VLAN_UPDATE_COLUMNS = {
    "vlan_id", "name", "description", "purpose", "subnet", "enabled",
}

MAB_DEVICE_UPDATE_COLUMNS = {
    "name", "description", "device_type",
    "assigned_vlan_id", "enabled", "expiry_date",
}

GROUP_VLAN_MAPPING_UPDATE_COLUMNS = {
    "group_name", "vlan_id", "priority", "description",
    "ldap_server_id", "enabled",
}
