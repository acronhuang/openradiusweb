"""Integration test fixtures: real Postgres via testcontainers.

These tests catch a class of bugs that mock-based unit tests can't:
SQL that compiles fine but blows up at execution time. Examples:
  - PR #32: `:name::type` casts that asyncpg's named-parameter
    preprocessor mangles ("syntax error at or near :")
  - PR #33: SELECT references a column the schema doesn't have
  - migration that worked in dev but breaks on a fresh apply

The container is spun up ONCE per test session (postgres start is the
slow part). Each test gets its own SAVEPOINT and rolls back at the end,
so tests don't interfere even though they share one DB.

CI / local requirements:
  - Docker daemon reachable
  - timescale/timescaledb:latest-pg15 image (or pulled on first run)

Tests are auto-skipped if Docker isn't available, so this conftest is
safe to ship even on machines without Docker. To skip even when Docker
is present (e.g. CI not configured for it), set ORW_SKIP_INTEGRATION=1.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# Match the prod image (docker-compose.yml). TimescaleDB extension is
# required because init.sql calls create_hypertable() on events, audit_log,
# and radius_auth_log.
PG_IMAGE = os.environ.get(
    "ORW_TEST_PG_IMAGE", "timescale/timescaledb:latest-pg15"
)


# ---------------------------------------------------------------------------
# Skip-if-no-Docker gate
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    if os.environ.get("ORW_SKIP_INTEGRATION"):
        return False
    try:
        import docker  # noqa: WPS433 — local import is intentional
        client = docker.from_env(timeout=2)
        client.ping()
        return True
    except Exception:
        return False


_DOCKER_OK = _docker_available()
_SKIP_REASON = (
    "Docker not available (or ORW_SKIP_INTEGRATION set). "
    "Skipping postgres integration tests."
)


def pytest_collection_modifyitems(config, items):
    """Auto-skip every test in this directory if Docker is unreachable."""
    if _DOCKER_OK:
        return
    skip = pytest.mark.skip(reason=_SKIP_REASON)
    here = Path(__file__).parent
    for item in items:
        try:
            item_path = Path(str(item.fspath))
        except Exception:
            continue
        if here in item_path.parents or item_path == here:
            item.add_marker(skip)


# ---------------------------------------------------------------------------
# Migration loader — same ordering rule as the contract test
# ---------------------------------------------------------------------------

def _migration_sql() -> str:
    """Concatenate all migration SQL in apply order (init first, then numbered)."""
    files = list(MIGRATIONS_DIR.glob("*.sql"))
    files.sort(key=lambda p: (p.name != "init.sql", p.name))
    parts = []
    for f in files:
        if f.name == "seed.sql":
            continue  # data only, not schema
        parts.append(f"-- {f.name}\n")
        parts.append(f.read_text(encoding="utf-8"))
        parts.append("\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Session-scoped: container + migrated DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Spin up postgres+timescaledb, apply all migrations, return DSN.

    Session-scoped because container start is the slow part (~5-10s).
    """
    if not _DOCKER_OK:
        pytest.skip(_SKIP_REASON)

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        PG_IMAGE,
        username="orw",
        password="orw_test",
        dbname="orw",
    )
    # TimescaleDB needs shared_preload_libraries set at server start.
    container.with_command(
        "postgres -c shared_preload_libraries=timescaledb"
    )
    container.start()
    try:
        # testcontainers gives us a psycopg2-style URL; convert to asyncpg.
        sync_url = container.get_connection_url()  # postgresql+psycopg2://...
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        # Apply migrations using a sync connection (one big transaction).
        # asyncpg's protocol forbids multi-statement DDL in one query, so
        # synchronous psycopg with autocommit is the simplest path.
        import psycopg2
        # Container exposes a host port; parse it from sync_url for psycopg.
        from urllib.parse import urlparse
        u = urlparse(sync_url.replace("postgresql+psycopg2://", "postgresql://"))
        conn = psycopg2.connect(
            host=u.hostname, port=u.port,
            user=u.username, password=u.password, dbname=u.path.lstrip("/"),
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(_migration_sql())
        conn.close()
        yield async_url
    finally:
        container.stop()


# ---------------------------------------------------------------------------
# Function-scoped: per-test session with rollback
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session(postgres_url) -> AsyncGenerator[AsyncSession, None]:
    """A clean AsyncSession per test, rolled back at the end.

    Engine is created per-test rather than session-scoped: pytest-asyncio
    creates a fresh event loop per test, and a session-scoped asyncpg
    engine ends up bound to a stale loop on the second test ("Event loop
    is closed" during teardown). The container is still session-scoped,
    so the only repeated cost is ~50ms of engine setup — worth it to
    keep tests independent.

    Repositories under test must NOT call session.commit() themselves —
    we rely on the FastAPI get_db() dependency for commits in prod, and
    on the rollback at fixture teardown here.
    """
    engine = create_async_engine(postgres_url, future=True)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def tenant_id(db_session) -> str:
    """Insert a fresh tenant inside this test's transaction and return its id.

    All write-side tables FK back to tenants(id), so a freshly minted UUID
    won't satisfy the constraint. Inserting per test keeps tests isolated:
    the transaction rollback at teardown deletes the tenant too.
    """
    from sqlalchemy import text
    name = f"test-{uuid4().hex[:12]}"
    result = await db_session.execute(
        text(
            "INSERT INTO tenants (name, display_name) "
            "VALUES (:name, :display) RETURNING id"
        ),
        {"name": name, "display": name},
    )
    return str(result.scalar())
