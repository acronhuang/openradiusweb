# Feature Migration Tracker

Tracks the migration of `services/gateway/routes/<resource>.py` files into the standard feature-oriented layout `services/gateway/features/<name>/` per [development-manual.md §10.6.3](development-manual.md#1063-migration-path-for-the-existing-flat-routes).

**Last updated:** 2026-04-28 (pilot migration of `auth/` + `profile/` complete)

## Status Legend

- `[ ]` Legacy — still in `services/gateway/routes/<file>.py`
- `[~]` In progress — partial migration; both old and new co-exist (avoid landing this state on `main`)
- `[x]` Migrated — old file deleted; lives under `services/gateway/features/<name>/`

## Routes (19 total)

Feature group numbers below reference [development-manual.md §2.2](development-manual.md#22-feature-mapping-table).

| Status | Legacy file | Target feature folder | Group | Notes |
|--------|-------------|------------------------|-------|-------|
| `[x]` | `routes/auth.py` | `features/auth/` | 1 — Auth & users | Pilot — canonical template; 13 pure-unit service tests pass |
| `[x]` | `routes/profile.py` | `features/auth/` (merged) | 1 — Auth & users | Merged into `features/auth/` per §2.2 |
| `[ ]` | `routes/devices.py` | `features/devices/` | 2 — Device inventory | Has NATS publisher → needs `events.py` |
| `[ ]` | `routes/policies.py` | `features/policies/` | 4 — Policy engine | Has `evaluator.py` (pure Layer 2) |
| `[ ]` | `routes/radius_auth_log.py` | `features/radius_auth_log/` | 5 — RADIUS auth | TimescaleDB hypertable reads |
| `[ ]` | `routes/group_vlan_mappings.py` | `features/group_vlan_mappings/` | 6 — Dynamic VLAN | Standard CRUD |
| `[ ]` | `routes/mab_devices.py` | `features/mab_devices/` | 7 — MAB | Standard CRUD |
| `[ ]` | `routes/coa.py` | `features/coa/` | 8 — CoA | Has NATS publisher → needs `events.py` |
| `[ ]` | `routes/ldap_servers.py` | `features/ldap_servers/` | 9 — RADIUS config | Standard CRUD |
| `[ ]` | `routes/radius_realms.py` | `features/radius_realms/` | 9 — RADIUS config | Standard CRUD |
| `[ ]` | `routes/nas_clients.py` | `features/nas_clients/` | 9 — RADIUS config | Standard CRUD |
| `[ ]` | `routes/vlans.py` | `features/vlans/` | 9 — RADIUS config | Standard CRUD |
| `[ ]` | `routes/freeradius_config.py` | `features/freeradius_config/` | 9 — RADIUS config | Has NATS publisher (config apply) |
| `[ ]` | `routes/certificates.py` | `features/certificates/` | 10 — Certificates | Crypto-heavy; reuse `shared/orw_common` atoms |
| `[ ]` | `routes/network_devices.py` | `features/network_devices/` | 11 — Switch management | Has NATS publishers (set_vlan, bounce_port) |
| `[ ]` | `routes/audit.py` | `features/audit/` | 12 — Audit & logs | Read-only (export endpoint) |
| `[ ]` | `routes/dot1x_overview.py` | `features/dot1x_overview/` | 13 — 802.1X overview | Aggregate queries only |
| `[ ]` | `routes/settings.py` | `features/settings/` | 15 — System settings | Standard CRUD |
| `[ ]` | `routes/health.py` | `features/health/` | 16 — Health & monitoring | Tiny — could merge into `main.py` instead |

**Migrated:** 2 / 19
**In progress:** 0
**Remaining:** 17

## Canonical template

The pilot migration of `features/auth/` is the reference implementation. New
features and subsequent migrations should mirror its structure:

```
services/gateway/features/auth/
├── __init__.py          # public API (auth_router, profile_router)
├── schemas.py           # re-exports from orw_common.models.auth + ROLE_PERMISSIONS
├── repository.py        # 14 single-responsibility DB atoms (Resolver/Query/Repository)
├── service.py           # use-case composition; raises domain exceptions, never HTTPException
├── routes.py            # thin Layer 3 — parse → call service → serialize
└── tests/
    ├── conftest.py      # feature-local fixtures (mock_redis, mock_db, test_client)
    ├── test_routes.py   # HTTP-level tests through ASGI
    └── test_service.py  # pure-unit tests against service layer (no FastAPI, no DB)
```

Key conventions established by the pilot:

- **`schemas.py`** is the data-shape surface. Re-exports from
  `orw_common.models.*` are fine when other services share the model;
  inline only when the model is feature-private.
- **`repository.py`** functions are atoms — one DB statement, one
  responsibility, no business logic, no exceptions beyond what
  asyncpg/SQLAlchemy raise. Names are verbs (`lookup_*`, `insert_*`,
  `update_*`, `delete_*`, `count_*`, `list_*`).
- **`service.py`** orchestrates atoms and raises domain exceptions
  (`NotFoundError`, `ConflictError`, `ValidationError`,
  `AuthenticationError`, `RateLimitError`). It does **not** import
  FastAPI or `HTTPException`.
- **`routes.py`** handlers are 5–15 lines: `Depends(...)` → call
  `service.X(...)` → wrap in response model. Domain exceptions are
  translated to HTTP status codes by the global handler in
  `gateway/main.py`.
- **`__init__.py`** exposes only what `gateway/main.py` (or other
  features) need — typically just routers. Internal symbols stay
  internal.
- **Tests:** `test_service.py` (no HTTP/DB) is the fast feedback loop;
  `test_routes.py` verifies the wire-level shape.

## Recommended order

Start with low-risk, high-clarity migrations to validate the template, then expand:

1. **`auth/` + `profile/` (merged)** — pilot. Low coupling, well-bounded; produces the canonical `features/<name>/` template that subsequent migrations copy.
2. **`health/`** — trivial; or fold into `main.py` and skip a feature folder entirely.
3. **CRUD-only group**: `vlans`, `nas_clients`, `mab_devices`, `group_vlan_mappings`, `ldap_servers`, `radius_realms`, `settings`, `audit` — same shape, easy to migrate one-per-PR.
4. **NATS-publishing routes**: `devices`, `coa`, `freeradius_config`, `network_devices` — exercise the `events.py` slot.
5. **Complex routes**: `policies` (with `evaluator.py`), `certificates` (crypto), `radius_auth_log` (Timescale), `dot1x_overview` (aggregate) — last, when the template is well-proven.

## How to update this file

When migrating a feature:

1. Change its row from `[ ]` → `[~]` when starting work.
2. After the PR lands and the legacy file is deleted, change to `[x]`.
3. Update the "Migrated / In progress / Remaining" counters.
4. Remove the corresponding entry from `LEGACY_ROUTES` in [scripts/check_no_new_routes.py](../scripts/check_no_new_routes.py).
5. Bump "Last updated" at the top.
