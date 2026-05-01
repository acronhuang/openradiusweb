"""Render the freeradius templates and validate them with `freeradius -CX`.

The templates expand into modules + sites whose validity depends on which
modules/policies the running freeradius binary actually has. Our prod
incidents (PR #36/#38/#39) all came from templates that rendered fine
as text but crashed the daemon at startup because they referenced
modules/methods that don't exist.

These tests render the same templates with realistic context and run
freeradius -CX inside the same image we ship to prod
(freeradius/freeradius-server:3.2.3) — the real check, not a mock.

Add a new test here whenever a template gains a capability flag or
references a new module. The marginal cost (~3 seconds in the warm
case) is much cheaper than catching it in a redeployment loop.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Default capability flags — minimum config the manager produces when the
# DB is empty (no LDAP servers, no realms) and rlm_python3 isn't bundled
# in the freeradius binary. This is the path the deployment hit on
# 2026-04-30 before any LDAP/realm was created.
# ---------------------------------------------------------------------------

_MINIMAL_RENDER_KW = dict(
    generated_at="2026-05-01T00:00:00Z",
    has_eap=False,
    has_python=False,
    ldap_modules=[],
    realms_enabled=False,
    max_connections=16,
)


def _site_default(jinja_env, **overrides) -> str:
    kw = {**_MINIMAL_RENDER_KW, **overrides}
    return jinja_env.get_template("site_default.j2").render(**kw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_minimal_site_default_passes_freeradius_check(jinja_env, freeradius_check):
    """Empty-DB / no-capability render must validate.

    This is the path the prod manager takes when the DB has no LDAP
    servers, no realms, and rlm_python3 isn't compiled in. PR #36 broke
    exactly this case (preprocess in accounting); PR #38 broke it again
    (remove_reply_message_authenticator missing); PR #39 broke a related
    template-render concern.
    """
    rendered = {
        "sites-enabled/default": _site_default(jinja_env),
    }
    result = freeradius_check(rendered)
    assert result.ok, (
        f"freeradius -CX rejected the minimal generated site_default "
        f"(exit {result.exit_code}).\n"
        f"Errors:\n  " + "\n  ".join(result.error_lines() or ["(none parsed)"])
    )


def test_realms_enabled_site_default_passes(jinja_env, freeradius_check):
    """When realms exist, site_default adds suffix/ntdomain/proxy stanzas.

    These stanzas reference policy fragments that ship with freeradius;
    catches the case where someone adds a new realm-related module
    reference that isn't actually bundled.
    """
    rendered = {
        "sites-enabled/default": _site_default(jinja_env, realms_enabled=True),
    }
    result = freeradius_check(rendered)
    assert result.ok, (
        f"freeradius -CX rejected site_default with realms_enabled=True "
        f"(exit {result.exit_code}).\n"
        f"Errors:\n  " + "\n  ".join(result.error_lines() or ["(none parsed)"])
    )


def test_clients_conf_with_one_nas_passes(jinja_env, freeradius_check):
    """The generated clients.conf must parse with at least one client.

    Catches accidental syntax mistakes in clients.conf.j2 (missing braces,
    duplicated key, malformed limit { } block).
    """
    clients_content = jinja_env.get_template("clients.conf.j2").render(
        generated_at="2026-05-01T00:00:00Z",
        nas_clients=[
            {
                "shortname": "test-switch",
                "name": "test-switch",
                "ipaddr": "10.0.0.1",
                "secret": "testing123",
                "nastype": "cisco",
                "description": "Smoke-test fixture",
            },
        ],
    )
    rendered = {
        "sites-enabled/default": _site_default(jinja_env),  # site needed for full parse
        "clients.conf": clients_content,
    }
    result = freeradius_check(rendered)
    assert result.ok, (
        f"freeradius -CX rejected the generated clients.conf "
        f"(exit {result.exit_code}).\n"
        f"Errors:\n  " + "\n  ".join(result.error_lines() or ["(none parsed)"])
    )


# ---------------------------------------------------------------------------
# Regression marker (proves the test would catch the original bug class)
# ---------------------------------------------------------------------------

def test_pr36_regression_simulation_is_caught(jinja_env, freeradius_check):
    """If someone re-introduces preprocess in accounting (PR #36), this fails.

    Not a "real" test of the templates as-shipped — it injects the broken
    pattern then asserts freeradius -CX rejects it. Belt-and-braces proof
    that this Phase 3 layer would catch the original bug class. If this
    test ever passes (i.e. `preprocess` becomes legal in 'accounting'),
    delete it; the assertion has lost meaning.
    """
    site = _site_default(jinja_env)
    # Same single-line replacement the actual fix avoided.
    bad = site.replace(
        "accounting {\n", "accounting {\n        preprocess\n", 1,
    )
    assert "preprocess" in bad, "test setup error — injection did not land"

    result = freeradius_check({"sites-enabled/default": bad})
    assert not result.ok, (
        "Expected freeradius -CX to reject 'preprocess' in accounting "
        "section, but it accepted the config. Either freeradius behavior "
        "changed or our injection no longer takes effect — investigate."
    )
    # Sanity: the rejection message should mention preprocess + accounting.
    combined = result.stdout.lower()
    assert "preprocess" in combined and "accounting" in combined, (
        f"Rejection happened but for an unexpected reason. Output:\n{result.stdout}"
    )
