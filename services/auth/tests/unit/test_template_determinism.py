"""Templates must render byte-identically across calls when the input
data is unchanged.

Why this matters: apply_configs() hashes the rendered output and only
SIGHUPs freeradius when the hash differs from what's already stored.
A non-deterministic template (e.g. one that interpolates `datetime.now()`
into a comment) defeats the hash check — every render produces a new
hash, every reconcile triggers a SIGHUP, and freeradius spam-reloads
every module on the 5-min reconcile interval. We hit exactly that on
2026-05-03; this test exists so a future timestamp regression fails CI
instead of production.

Scope: Jinja templates only. The wider apply_configs path also needs
to be deterministic for the same reason, but that's checked separately
(integration test that mocks the DB and runs the full pipeline twice).
"""
from __future__ import annotations

from pathlib import Path

import pytest


jinja2 = pytest.importorskip("jinja2")

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[2] / "freeradius" / "templates"
)


# Per-template minimum context that lets it render without raising. The
# point isn't to verify correctness of the rendered config (other tests
# do that) — only to verify that the same input twice produces the same
# bytes. So the contexts are deliberately minimal.
_RENDER_CASES: dict[str, dict] = {
    "clients.conf.j2": {"clients": [], "mab_devices": [], "generated_at": "fixed"},
    "eap.j2": {"generated_at": "fixed"},
    "ldap.j2": {"servers": [], "generated_at": "fixed"},
    "proxy.conf.j2": {"realms": [], "generated_at": "fixed"},
    "python.j2": {"generated_at": "fixed"},
    "site_default.j2": {
        "use_orw_module": True,
        "ldap_enabled": False,
        "generated_at": "fixed",
    },
    "site_inner_tunnel.j2": {
        "use_orw_module": True,
        "ldap_enabled": False,
        "generated_at": "fixed",
    },
}


@pytest.fixture(scope="module")
def env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
    )


@pytest.mark.parametrize("template_name,context", list(_RENDER_CASES.items()))
def test_template_renders_deterministically(env, template_name, context):
    """Same template + same context twice → byte-identical output."""
    tpl = env.get_template(template_name)
    first = tpl.render(**context)
    second = tpl.render(**context)
    assert first == second, (
        f"{template_name} rendered different output on identical inputs. "
        f"Likely a timestamp / random value snuck back into the template — "
        f"that defeats the hash-based change detection in apply_configs() "
        f"and re-introduces the SIGHUP storm bug from PR #87."
    )


def test_no_timestamp_comment_in_templates():
    """Headers must not contain `# Generated at: {{ generated_at }}`.

    Defense in depth — even if the determinism test above gets
    accidentally weakened (e.g. someone passes time.time() as
    generated_at), this lint catches the pattern at the source.
    """
    bad: list[str] = []
    for tpl in _TEMPLATE_DIR.glob("*.j2"):
        head = tpl.read_text(encoding="utf-8").splitlines()[:5]
        if any("generated_at" in line for line in head):
            bad.append(tpl.name)
    assert not bad, (
        f"These templates still embed a `generated_at` value in their "
        f"first 5 lines, which makes the rendered config non-deterministic: "
        f"{bad}. Remove the timestamp from the template header — the "
        f"freeradius_config table already records last_applied_at."
    )
