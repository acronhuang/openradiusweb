"""Contract test: every static-path route under a parameterised
parent must be matchable.

Why this exists: PR #97 added `GET /mab-devices/export-csv` after
the existing `GET /mab-devices/{device_id}`. FastAPI matches routes
in declaration order, so /export-csv was swallowed by /{device_id}
which then tried to parse "export-csv" as a UUID and 422'd. The
unit + integration tests didn't catch it because they go through
the service layer directly, bypassing the router.

This test inspects the ROUTER (real FastAPI app) to verify that for
every endpoint that has both a `/{<param>}` route AND a literal
sub-path route at the same depth, the literal one is registered
FIRST (= will be matched first by Starlette).

Add a new entry to KNOWN_PATTERNS whenever a feature gains a new
"static next to dynamic" route. The test will fail loudly if a
future PR re-introduces the same ordering bug.
"""
from __future__ import annotations

import re

import pytest


# (parent_prefix, set of static sub-segments that must come before
# the {param} catch-all). Format: parent has both `/{x}` route(s) AND
# at least one of these literal segments.
KNOWN_PATTERNS = [
    (
        "/api/v1/mab-devices",
        {"check", "bulk-import", "import-csv", "export-csv"},
    ),
    # Add new entries here as features grow:
    # ("/api/v1/certificates", {"export", "active"}),
]


@pytest.fixture(scope="module")
def app_routes():
    """The full FastAPI app's route table, in declaration order."""
    from main import app
    return list(app.routes)


def _routes_under(prefix: str, app_routes) -> list:
    """All routes whose path starts with `prefix` (in declaration
    order). Excludes Mount / WebSocket; only HTTP routes."""
    out = []
    for r in app_routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        if path == prefix or path.startswith(prefix + "/"):
            out.append(r)
    return out


_PARAM_RE = re.compile(r"^\{[^}]+\}$")


def _is_param_segment(segment: str) -> bool:
    """A path segment of the form `{name}`. Excludes mixed segments
    like `{id}.json` (none of those exist in this codebase, but be
    explicit anyway)."""
    return bool(_PARAM_RE.match(segment))


@pytest.mark.parametrize("parent,static_segments", KNOWN_PATTERNS)
def test_static_routes_declared_before_param_catchall(
    parent, static_segments, app_routes,
):
    """For each static segment listed, find its first registration
    index in app.routes and assert it's lower than the index of the
    `{device_id}` (or whatever the param is) route.
    """
    routes = _routes_under(parent, app_routes)
    if not routes:
        pytest.skip(f"No routes under {parent} — feature not loaded?")

    # Find the index (in declaration order) of the FIRST route whose
    # final segment is a {param} placeholder.
    first_param_idx = None
    for idx, r in enumerate(routes):
        rest = r.path.removeprefix(parent).lstrip("/")
        if not rest:
            continue  # this is the bare collection (`""`)
        last_seg = rest.split("/")[-1]
        if _is_param_segment(last_seg) and "/" not in rest:
            first_param_idx = idx
            break

    if first_param_idx is None:
        pytest.skip(
            f"{parent} has no /{{param}} catch-all — ordering not relevant"
        )

    # For each static segment listed, find its index. It must be < first_param_idx.
    failures: list[str] = []
    for static in static_segments:
        # The static may be at depth 1 (e.g. /export-csv) OR depth 2
        # (e.g. /check/{mac_address}). We care about whether its
        # FIRST PATH SEGMENT after parent matches the literal — that's
        # what Starlette will compare.
        static_idx = None
        for idx, r in enumerate(routes):
            rest = r.path.removeprefix(parent).lstrip("/")
            if not rest:
                continue
            first_seg = rest.split("/")[0]
            if first_seg == static:
                static_idx = idx
                break
        if static_idx is None:
            failures.append(
                f"  - expected literal segment '{static}' under {parent}, "
                f"none registered"
            )
            continue
        if static_idx >= first_param_idx:
            param_path = routes[first_param_idx].path
            static_path = routes[static_idx].path
            failures.append(
                f"  - {static_path} (idx {static_idx}) is registered "
                f"AFTER {param_path} (idx {first_param_idx}). "
                f"Starlette will match the param route first and "
                f"422 with `uuid_parsing` on the literal segment. "
                f"Move {static_path} above {param_path} in routes.py."
            )
    assert not failures, (
        f"\nRoute ordering bug in {parent}:\n" + "\n".join(failures)
    )
