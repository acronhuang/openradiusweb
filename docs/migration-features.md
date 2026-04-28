# Feature Migration Tracker

Tracks the migration of `services/gateway/routes/<resource>.py` files into the standard feature-oriented layout `services/gateway/features/<name>/` per [development-manual.md §10.6.3](development-manual.md#1063-migration-path-for-the-existing-flat-routes).

**Last updated:** 2026-04-28

## Status Legend

- `[ ]` Legacy — still in `services/gateway/routes/<file>.py`
- `[~]` In progress — partial migration; both old and new co-exist (avoid landing this state on `main`)
- `[x]` Migrated — old file deleted; lives under `services/gateway/features/<name>/`

## Routes (19 total)

Feature group numbers below reference [development-manual.md §2.2](development-manual.md#22-feature-mapping-table).

| Status | Legacy file | Target feature folder | Group | Notes |
|--------|-------------|------------------------|-------|-------|
| `[ ]` | `routes/auth.py` | `features/auth/` | 1 — Auth & users | Pilot candidate (smallest, most self-contained) |
| `[ ]` | `routes/profile.py` | `features/auth/` (merge) | 1 — Auth & users | Belongs with `auth/` per §2.2 |
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

**Migrated:** 0 / 19
**In progress:** 0
**Remaining:** 19

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
