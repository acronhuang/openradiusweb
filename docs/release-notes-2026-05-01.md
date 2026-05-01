# Release Notes — 2026-05-01

## TL;DR

Nine PRs cover three threads of work that came out of the 2026-04-30
deployment to 192.168.0.250:

1. **Two production bugs** discovered during post-deploy UI smoke
   (Realm create, LDAP test connection)
2. **Three-layer integration test foundation** so the bug classes
   that hit prod (PRs #31/#32/#33/#36/#38/#39/#40/#46) get caught at
   PR-time instead of at deploy
3. **Three operational additions** — backup/restore, opt-in Prom+Grafana,
   FreeRADIUS image with rlm_python3 actually bundled

## Merge order

Independent fixes can land first; test/CI/ops work has internal
ordering for cleanest review:

| Order | PR | Reason |
|---|---|---|
| 1 | [#42](https://github.com/acronhuang/openradiusweb/pull/42) | Bug fixes — independent, unblocks UI |
| 2 | [#46](https://github.com/acronhuang/openradiusweb/pull/46) | Bug fix — independent |
| 3 | [#41](https://github.com/acronhuang/openradiusweb/pull/41) | Phase 1 contracts — test infra base |
| 4 | [#43](https://github.com/acronhuang/openradiusweb/pull/43) | Phase 2 integration — adds testcontainers dep |
| 5 | [#44](https://github.com/acronhuang/openradiusweb/pull/44) | Phase 3 freeradius -CX |
| 6 | [#45](https://github.com/acronhuang/openradiusweb/pull/45) | CI wiring — depends on test files from #41/#43/#44 |
| 7 | [#47](https://github.com/acronhuang/openradiusweb/pull/47) | Backup scripts — independent |
| 8 | [#48](https://github.com/acronhuang/openradiusweb/pull/48) | Monitoring (opt-in) — independent |
| 9 | [#49](https://github.com/acronhuang/openradiusweb/pull/49) | FreeRADIUS image swap — highest risk, validate on non-prod first |

After PR #41 + #46 both land, a 2-line cleanup PR removes the
`_LDAP_PR46_PENDING` skip from the contract test (noted in #41's PR
comment thread).

---

## Bug fixes

### [#42](https://github.com/acronhuang/openradiusweb/pull/42) — `fix: realm proxy_port + LDAP test error display`

Two production bugs surfaced during the same UI walkthrough:

1. **Realm Create rejected `Local` realms** with `proxy_port: Input
   should be a valid integer`. The Add Realm form clears proxy fields
   when `realm_type='local'` (sends `null`), but `RealmCreate.proxy_port`
   was typed `int = 1812` — Pydantic v2 won't coerce null → int.
   Fix: `Optional[int] = Field(1812, ge=1, le=65535)`. DB column was
   already nullable.
2. **LDAP test always showed `Connection failed: Unknown error`**
   even on real failures. Backend returned `error_message` in the
   JSON; frontend was reading `r.error` (undefined). Fix: read
   `r.error_message` to match the response shape.

### [#46](https://github.com/acronhuang/openradiusweb/pull/46) — `fix(ldap): require bind_dn + bind_password on Create`

Latent 500 path: `LDAPServerCreate` typed both fields as
`Optional[str]` but the schema columns are `NOT NULL`. A blank
submission would pass Pydantic and then 500 at INSERT with
`null value in column "bind_dn" violates not-null`.

Fix: model fields are now `Field(..., min_length=1)`; frontend Create
dialog adds `required: true` for `bind_password` (Edit dialog still
treats blank as "unchanged" to preserve UX). 6 new model-level tests
lock in the contract; existing 13 service tests still pass.

Surfaced during the Phase 1 contract-test sweep (see #41 below).

---

## Test infrastructure (three phases)

Three independent layers, each catching a distinct bug class with
different cost/coverage trade-offs:

| Phase | PR | What it catches | Cost (cold/warm) |
|---|---|---|---|
| 1 — Contract | [#41](https://github.com/acronhuang/openradiusweb/pull/41) | Pydantic ↔ DB column type/name/nullability mismatch | <1s, no Docker |
| 2 — Integration | [#43](https://github.com/acronhuang/openradiusweb/pull/43) | SQL that compiles but blows up at execution | ~75s / ~18s, Docker |
| 3 — `freeradius -CX` | [#44](https://github.com/acronhuang/openradiusweb/pull/44) | Template ↔ running-config drift | ~10s / ~5s, Docker |

### [#41](https://github.com/acronhuang/openradiusweb/pull/41) — `test(contracts): catch model<->schema misalignment at PR time`

Parses `migrations/*.sql` and verifies every Pydantic `*Create/*Update`
field maps to a real column with a compatible type **and** consistent
nullability. No DB needed — fails fast in CI when:

- A model field name doesn't exist as a column (PR #31, #33 class)
- A model field type can't bind to the column type (PR #40 class —
  `bool` bound to a `VARCHAR` enum)
- A model field is `Optional[X]` but the DB column is `NOT NULL`
  (PR #46 class — Pydantic accepts null, asyncpg rejects)

Verified locally: simulating PR #40's regression (changing
`tls_require_cert` back to `bool`) is caught instantly with
`bool compatible with VARCHAR(20)? False`.

### [#43](https://github.com/acronhuang/openradiusweb/pull/43) — `test(integration): Phase 2 — real Postgres via testcontainers`

Spins up a real Postgres + TimescaleDB container per test session,
applies all migrations, and runs each test inside a transaction that
rolls back at teardown. Per-test isolation without truncate/recreate.

CRUD smoke tests for `nas_clients`, `ldap_servers`, `vlans` (more to
add incrementally). Verified locally that simulating PR #32's regression
(reverting `CAST(:subnet AS cidr)` → `:subnet::cidr`) makes the vlan
smoke test fail with the *exact* prod error: `syntax error at or near ":"`.

Auto-skips when Docker is unavailable so dev machines without Docker
aren't blocked.

### [#44](https://github.com/acronhuang/openradiusweb/pull/44) — `test(integration): Phase 3 — freeradius -CX template validation`

Renders each Jinja2 template with realistic context, mounts into a
stock freeradius container, and runs `freeradius -CX` against it —
the exact validator the daemon uses at startup.

Catches PR #36/#38/#39 class: templates that render to text fine but
break radiusd at startup because they reference modules/methods that
don't actually exist in the running config. Includes a regression-
simulation test that injects PR #36's `preprocess` in `accounting`
and asserts -CX rejects it with the exact prod error message.

### [#45](https://github.com/acronhuang/openradiusweb/pull/45) — `ci: wire Phase 1/2/3 test jobs into CI`

Adds three independent jobs to `.github/workflows/ci.yml`:
`contract-tests`, `integration-postgres`, `integration-freeradius`.
Pre-pulls the heavy images outside the test step so a slow first
pull doesn't get blamed on a flaky fixture. Independent so a
Phase 2 break can't mask a Phase 3 break.

After #41 + #43 + #44 + #45 all merge, every subsequent PR auto-runs
all three test phases plus existing `lint` / `pre-commit` /
`unit-tests` jobs.

---

## Operations

### [#47](https://github.com/acronhuang/openradiusweb/pull/47) — `feat(ops): backup + restore scripts`

Two scripts:
- `scripts/backup.sh` — single tar.gz containing `pg_dump`, both
  freeradius volumes, and `.env.production`. Cron example in the
  header for daily 02:30
- `scripts/restore.sh` — companion that restores `.env.production`
  *first* (saves pre-restore copy as `.env.production.pre-restore.<ts>`
  so an aborted restore is recoverable), replays the SQL dump, wipes
  + repopulates volumes, brings everything back up

`pg_dump` rather than a volume copy so backups survive postgres
major-version bumps. `.env.production` included because without it,
the restored DB password doesn't match what services try to use —
the most-forgotten file in deploy backups.

### [#48](https://github.com/acronhuang/openradiusweb/pull/48) — `feat(ops): opt-in Prometheus + Grafana monitoring stack`

Gateway already exposed `/metrics` via `prometheus_fastapi_instrumentator`
but nothing was scraping it. Adds the consumption side as an **opt-in
compose profile** — default `up -d` is unchanged.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
    --profile monitoring up -d
```

Prometheus on `127.0.0.1:9090`, Grafana on `127.0.0.1:3000` (admin
password from `$GRAFANA_PASSWORD`). Datasource auto-provisioned. Both
services bound to localhost only — exposing them to the network is the
operator's explicit call.

Postgres/Redis exporters and starter dashboards deliberately deferred —
build dashboards once we know what to alert on.

### [#49](https://github.com/acronhuang/openradiusweb/pull/49) — `feat(freeradius): debian base + rlm_python3 (also fixes broken detection)`

**The most consequential PR in this batch.** Discovered during
implementation that **two stacked bugs** had been silently disabling
the orw rlm_python3 path in production all along:

1. The upstream `freeradius/freeradius-server:3.2.3` image **doesn't
   bundle `rlm_python3.so`** at all
2. PR #37's `_rlm_python3_available()` checks `radiusd -v` for the
   string `"rlm_python3"`, but **`-v` doesn't list compiled-in
   modules** — just the version banner. So the function returned
   False on every supported image, including ones that DO have the
   module

Together: `has_python` was pinned to False everywhere, the python
module config was always skipped, and `/opt/orw/rlm_orw.py` (which
holds policy decisions, VLAN assignment, accounting hooks) **never
ran in production**.

Fix:
- Switch base to `debian:bookworm-slim` + apt `freeradius-python3
  freeradius-ldap`. Symlinks `/etc/freeradius/{mods-enabled,...}` →
  `/etc/freeradius/3.0/*` so existing entrypoint paths work without
  rewrites
- Build-time `test -f /usr/lib/freeradius/rlm_python3.so` so a future
  Debian package layout shift fails LOUDLY at build instead of
  silently re-creating the original restart-loop bug
- New `_rlm_python3_available()` checks the .so directly across
  standard Debian / multi-arch paths — works regardless of how the
  underlying freeradius binary reports versions

Also adds `.gitattributes` pinning `*.sh` / `Dockerfile*` / `*.yml`
to LF, so a Windows working-tree checkout doesn't break Linux container
builds via CRLF shebangs (`/bin/bash\r: no such file`). Hit this
during local validation.

**Risk and rollback:** Image goes from ~70 MB → ~110 MB; FreeRADIUS
3.2.3 → 3.2.1 (Debian's pin, same minor). Rollback is a single-line
revert of `Dockerfile.freeradius`. The detection fix is a real bug fix
that should stand either way.

---

## Migration steps

Most PRs are no-op for operators — code lands and CI / deploy picks
it up. Only two need explicit operator action:

### After #48 merges (optional)

Set `GRAFANA_PASSWORD` in `.env.production` *before* enabling the
monitoring profile:

```bash
echo "GRAFANA_PASSWORD=$(openssl rand -base64 24)" >> .env.production
docker compose -f docker-compose.prod.yml --env-file .env.production \
    --profile monitoring up -d
```

Default falls back to `admin` if unset — fine for an SSH-tunneled
Grafana on a hardened host, not OK for anything broader.

### After #49 merges (required for prod)

Rebuild the freeradius image (one-time):

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
    build --no-cache freeradius
docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d freeradius
```

Confirm `rlm_python3` is loaded by tailing the startup logs:

```bash
docker logs orw-freeradius 2>&1 | grep -E "rlm_python3|Loading module \"orw\""
```

If the orw module isn't there, the manager skipped its config
generation — re-run by restarting the watcher:

```bash
docker compose restart freeradius_config_watcher
```

---

## What's deliberately not in this batch

- **Phase 2 smoke tests for `radius_realms`, `mab_devices`, `policies`,
  `group_vlan_mappings`, `coa`, `radius_auth_log`** — pattern is
  established in #43; add incrementally as each feature gets touched
- **Postgres / Redis / FreeRADIUS Prometheus exporters** — add once
  the gateway dashboards prove the loop works (#48 ships infrastructure,
  not opinions)
- **Starter Grafana dashboards** — building good ones is operational
  work that should follow real metrics observation (#48 ships the
  provisioning hook so JSONs can be dropped without compose churn)
- **Alerting rules** — same reasoning as dashboards
- **Phase 3 test variant against the new Debian freeradius image
  from #49** — worth a tiny follow-up PR after #49 lands; today's
  Phase 3 still targets the upstream 3.2.3 image
