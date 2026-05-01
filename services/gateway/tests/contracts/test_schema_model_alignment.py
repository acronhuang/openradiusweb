"""Contract test: every Pydantic *Create model field must be storable
in the corresponding postgres column.

This catches the class of bug we hit live during the 2026-04-30 deploy:
- PR #31: model field name didn't match DB column name
- PR #33: same — read columns named differently from what schema declares
- PR #40: model field typed as bool, schema column is VARCHAR enum

asyncpg refuses to coerce mismatched types and rejects the request with
HTTP 500. Unit tests pass because they mock the DB. The bug only
surfaces in production. This test runs against parsed schema + model
introspection — no DB needed — and rejects the misalignment at PR time.

Limitations:
- Doesn't catch SEMANTIC errors (e.g. wrong default value).
- Doesn't catch INSERT/UPDATE SQL with wrong column names — only the
  model-field side. Pair with a real-postgres integration test for that.
- Skips fields handled via column_map (request field renamed to a
  different DB column on its way in). Those are listed per-model below.
"""
from __future__ import annotations

import re
from pathlib import Path
from datetime import date, datetime, time
from typing import Any, NamedTuple, Optional, Union, get_args, get_origin
from uuid import UUID

import pytest
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "migrations"


# ---------------------------------------------------------------------------
# Schema parser
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\n\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
# `ALTER TABLE foo ADD COLUMN [IF NOT EXISTS] bar TYPE [NOT NULL] ...;`
# Group 4 captures the rest of the line so we can scan it for "NOT NULL".
_ALTER_ADD_COLUMN_RE = re.compile(
    r"""ALTER\s+TABLE\s+(\w+)\s+
        ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?
        (\w+)\s+
        ([A-Z]+(?:\s*\([^)]*\))?(?:\s*\[\])?)
        ([^;]*)
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Match a column definition line like "  name VARCHAR(255) NOT NULL,"
# Group 1 = column name; group 2 = pg type; group 3 = the rest of the line
# (constraints / defaults / etc.) — scanned for "NOT NULL" / "PRIMARY KEY".
_COLUMN_RE = re.compile(
    r"""^\s*
        ([a-z_][a-z_0-9]*)\s+              # column name
        ([A-Z]+(?:\s*\([^)]*\))?(?:\s*\[\])?)  # pg type, e.g. VARCHAR(50), INTEGER, TEXT[]
        (.*)$                              # rest: NOT NULL / DEFAULT / REFERENCES / etc.
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Lines that aren't columns — table-level constraints, triggers, etc.
_NON_COLUMN_PREFIX_RE = re.compile(
    r"^\s*(?:CONSTRAINT|UNIQUE|PRIMARY|FOREIGN|CHECK|--)",
    re.IGNORECASE,
)


def _is_not_null(rest_of_line: str) -> bool:
    """`NOT NULL` or `PRIMARY KEY` (which implies NOT NULL) in the trailing column spec."""
    upper = rest_of_line.upper()
    return "NOT NULL" in upper or "PRIMARY KEY" in upper


class ColumnInfo(NamedTuple):
    pg_type: str          # uppercase, e.g. "VARCHAR(255)" / "TEXT[]"
    nullable: bool        # True unless the column is NOT NULL or PRIMARY KEY


def parse_schema() -> dict[str, dict[str, ColumnInfo]]:
    """Return {table_name: {column_name: ColumnInfo(pg_type, nullable)}}.

    Walks every .sql in migrations/ and extracts CREATE TABLE statements.
    Later migrations override earlier ones for the same table (matches
    actual `apply` order).
    """
    schema: dict[str, dict[str, ColumnInfo]] = {}
    # init.sql is the base schema; numbered migrations apply on top.
    # Plain alphabetical sort would put `init.sql` AFTER `003_*.sql` and
    # silently overwrite ALTER-added columns. Order init first explicitly.
    sql_files = list(MIGRATIONS_DIR.glob("*.sql"))
    sql_files.sort(key=lambda p: (p.name != "init.sql", p.name))
    for sql_file in sql_files:
        if sql_file.name == "seed.sql":
            continue  # data only, no schema
        sql = sql_file.read_text(encoding="utf-8")
        for table_match in _CREATE_TABLE_RE.finditer(sql):
            table_name = table_match.group(1).lower()
            body = table_match.group(2)
            cols: dict[str, ColumnInfo] = {}
            for line in body.split("\n"):
                if _NON_COLUMN_PREFIX_RE.match(line):
                    continue
                col_match = _COLUMN_RE.match(line)
                if col_match:
                    cols[col_match.group(1).lower()] = ColumnInfo(
                        pg_type=col_match.group(2).strip().upper(),
                        nullable=not _is_not_null(col_match.group(3)),
                    )
            if cols:
                schema[table_name] = cols
        # Apply ALTER TABLE ADD COLUMN (additions are non-destructive,
        # idempotent in this codebase — IF NOT EXISTS everywhere).
        for alter_match in _ALTER_ADD_COLUMN_RE.finditer(sql):
            table_name = alter_match.group(1).lower()
            col_name = alter_match.group(2).lower()
            schema.setdefault(table_name, {})[col_name] = ColumnInfo(
                pg_type=alter_match.group(3).strip().upper(),
                nullable=not _is_not_null(alter_match.group(4)),
            )
    return schema


# ---------------------------------------------------------------------------
# Type compatibility
# ---------------------------------------------------------------------------

# pg type prefix → set of compatible Python types.
# Match by prefix so VARCHAR(50), VARCHAR(255), TEXT, etc. all map.
_PG_PYTHON_COMPAT: list[tuple[str, set[type]]] = [
    ("BOOLEAN", {bool}),
    ("BOOL", {bool}),
    ("INTEGER", {int}),
    ("BIGINT", {int}),
    ("SMALLINT", {int}),
    ("INT", {int}),
    ("NUMERIC", {int, float}),
    ("DECIMAL", {int, float}),
    ("REAL", {float}),
    ("DOUBLE", {float}),
    ("VARCHAR", {str}),
    ("CHAR", {str}),
    ("TEXT", {str, list, dict}),  # TEXT can hold serialized JSON
    ("UUID", {str, UUID}),
    ("INET", {str}),
    ("CIDR", {str}),
    ("MACADDR", {str}),
    ("CITEXT", {str}),
    ("JSONB", {str, dict, list}),
    ("JSON", {str, dict, list}),
    ("TIMESTAMPTZ", {str, datetime}),  # asyncpg accepts both
    ("TIMESTAMP", {str, datetime}),
    ("DATE", {str, date}),
    ("TIME", {str, time}),
    ("BYTEA", {bytes, str}),
]


def _python_type_compatible_with_pg(python_type: type, pg_type: str) -> bool:
    """Strip Optional / Annotated / Union noise; check via prefix table."""
    # Unwrap Optional[X] = Union[X, None]
    origin = get_origin(python_type)
    if origin is Union:
        args = [a for a in get_args(python_type) if a is not type(None)]
        # All non-None branches must individually be compatible
        return all(_python_type_compatible_with_pg(a, pg_type) for a in args)

    # Generic types like list[str], dict[str, Any] — keep the origin
    if origin is list:
        python_type = list
    elif origin is dict:
        python_type = dict

    pg_type_upper = pg_type.upper()
    # TEXT[] / VARCHAR[] etc. — array types, accept Python list
    if pg_type_upper.endswith("[]"):
        return python_type is list

    for prefix, compat_types in _PG_PYTHON_COMPAT:
        if pg_type_upper.startswith(prefix):
            return python_type in compat_types
    # Unknown pg type — be permissive rather than fail
    return True


# ---------------------------------------------------------------------------
# Model → table mapping
# ---------------------------------------------------------------------------
#
# Each entry says: when the gateway accepts an instance of <model>, the
# fields will (eventually) get INSERTed/UPDATEd into <table>. Some
# request fields get renamed via column_map in the repository — list
# those exceptions in `field_to_column`.
#
# `extra_skip` = field names known not to land in the table (e.g. computed
# server-side, used only for validation). Skip every check for them.
#
# `nullable_skip` = fields where the model is intentionally Optional but
# the DB column is NOT NULL. Use this only for fields that are filled in
# by a route/service layer before INSERT (so user-facing Pydantic doesn't
# need to require them). For the generic case where the API just accepts
# null and 500s downstream, FIX THE MODEL — that's the point of the check.

from orw_common.models.ldap_server import LDAPServerCreate, LDAPServerUpdate
from orw_common.models.nas_client import NASClientCreate, NASClientUpdate
from orw_common.models.radius_realm import RealmCreate, RealmUpdate
from orw_common.models.mab_device import MabDeviceCreate, MabDeviceUpdate
from orw_common.models.policy import PolicyCreate, PolicyUpdate
from orw_common.models.vlan import VlanCreate, VlanUpdate
from orw_common.models.group_vlan_mapping import (
    GroupVlanMappingCreate, GroupVlanMappingUpdate,
)


# (model, table, field_to_column rename map, extra_skip, nullable_skip)
Contract = tuple[type[BaseModel], str, dict[str, str], set[str], set[str]]

# TODO: drop the bind_dn / bind_password entries from nullable_skip once
# PR #46 (fix(ldap): require bind_dn + bind_password on Create) merges.
# That PR makes both fields required on the Create model, which closes
# the nullability mismatch and makes the skip unnecessary.
_LDAP_PR46_PENDING = {"bind_dn", "bind_password"}


CONTRACTS: list[Contract] = [
    (
        LDAPServerCreate, "ldap_servers",
        {"bind_password": "bind_password_encrypted"},
        set(),
        _LDAP_PR46_PENDING,
    ),
    (
        LDAPServerUpdate, "ldap_servers",
        {"bind_password": "bind_password_encrypted"},
        set(),
        # Update is partial by design — Optional fields shouldn't get
        # nullability-checked. Mark them all as expected exceptions.
        {"bind_dn", "bind_password", "name", "host", "base_dn"},
    ),
    (
        NASClientCreate, "radius_nas_clients",
        {"shared_secret": "secret_encrypted"},
        set(),
        set(),
    ),
    (
        NASClientUpdate, "radius_nas_clients",
        {"shared_secret": "secret_encrypted"},
        set(),
        # Same partial-update logic as LDAPServerUpdate.
        {"name", "ip_address", "shared_secret"},
    ),
    (
        RealmCreate, "radius_realms",
        {"proxy_secret": "proxy_secret_encrypted"},
        set(),
        set(),
    ),
    (
        RealmUpdate, "radius_realms",
        {"proxy_secret": "proxy_secret_encrypted"},
        set(),
        {"name", "realm_type"},  # required-on-create columns; Update may omit
    ),
    (
        MabDeviceCreate, "mab_devices",
        {},
        set(),
        set(),
    ),
    (
        MabDeviceUpdate, "mab_devices",
        {},
        set(),
        {"mac_address"},  # required-on-create column
    ),
    (
        PolicyCreate, "policies",
        {},
        # `conditions` / `match_actions` / `no_match_actions` are typed as
        # list[Pydantic model] but stored as JSONB — list-of-dicts is the
        # actual on-the-wire shape, which JSONB accepts.
        set(),
        set(),
    ),
    (
        PolicyUpdate, "policies",
        {},
        set(),
        # All NOT NULL on Create, partial-update Optional here.
        {"name", "conditions", "match_actions"},
    ),
    (
        VlanCreate, "vlans",
        {},
        set(),
        set(),
    ),
    (
        VlanUpdate, "vlans",
        {},
        set(),
        {"vlan_id", "name"},
    ),
    (
        GroupVlanMappingCreate, "group_vlan_mappings",
        {},
        set(),
        set(),
    ),
    (
        GroupVlanMappingUpdate, "group_vlan_mappings",
        {},
        set(),
        # group_vlan_mappings is the strictest table — every column is
        # NOT NULL even where defaults are present. Partial Update is fine.
        {"group_name", "vlan_id", "priority", "enabled"},
    ),
    # Certificate models pass through `crypto.parse_cert_metadata`
    # before INSERT, so the model fields don't directly map to columns.
    # Skip — but worth a separate test that verifies parsed metadata
    # keys land in the right columns.
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_optional(annotation: Any) -> bool:
    """True if the Pydantic annotation accepts None (Optional[X] / X | None)."""
    origin = get_origin(annotation)
    return origin is Union and type(None) in get_args(annotation)


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def schema() -> dict[str, dict[str, ColumnInfo]]:
    s = parse_schema()
    assert s, "schema parser returned empty — migrations/ not found?"
    return s


@pytest.mark.parametrize(
    "model,table,field_to_column,extra_skip,nullable_skip",
    CONTRACTS,
    ids=lambda item: getattr(item, "__name__", str(item))[:40],
)
def test_model_fields_align_with_table_columns(
    schema, model, table, field_to_column, extra_skip, nullable_skip,
):
    """Every Pydantic field maps to an existing column with a compatible type
    AND with consistent nullability.

    Two failure modes covered:
      1. **Type / name mismatch** — model field doesn't exist in the table or
         types don't line up (PR #31 / #33 / #40 class).
      2. **Nullability mismatch** — model field is Optional[X] but the DB
         column is NOT NULL. Pydantic accepts None, asyncpg rejects with
         "null value violates not-null constraint" (PR #46 class).
    """
    assert table in schema, (
        f"Table {table!r} not found in migrations. "
        f"Known tables: {sorted(schema)[:10]}..."
    )
    columns = schema[table]

    model_fields = model.model_fields  # type: ignore[attr-defined]
    failures: list[str] = []

    for field_name, field_info in model_fields.items():
        if field_name in extra_skip:
            continue
        column_name = field_to_column.get(field_name, field_name)
        if column_name not in columns:
            failures.append(
                f"  field {field_name!r} -> column {column_name!r} "
                f"NOT in table {table!r}"
            )
            continue
        col = columns[column_name]
        py_type = field_info.annotation
        # 1. Type compatibility
        if not _python_type_compatible_with_pg(py_type, col.pg_type):
            failures.append(
                f"  field {field_name!r} ({py_type}) "
                f"NOT compatible with column {column_name!r} ({col.pg_type})"
            )
        # 2. Nullability — only fails when model accepts None but DB doesn't.
        if (
            field_name not in nullable_skip
            and _is_optional(py_type)
            and not col.nullable
        ):
            failures.append(
                f"  field {field_name!r} is Optional[...] but column "
                f"{column_name!r} is NOT NULL — API will accept null and "
                f"asyncpg will 500 with 'null value violates not-null'"
            )

    if failures:
        msg = (
            f"\n{model.__name__} <-> {table} contract violations:\n"
            + "\n".join(failures)
            + "\n\nFix either the model field type/nullability OR the schema/"
            "column_map. See PR #31, #33, #40, #46 for examples of this bug "
            "class."
        )
        pytest.fail(msg)
