"""Regression test: forbid the `:foo::inet` named-param + typecast pattern.

asyncpg's named-parameter preprocessor (used by SQLAlchemy's asyncpg dialect)
splits parameter names at any non-identifier character. The trailing `::`
PostgreSQL typecast confuses it: `:nas_ip::inet` ends up *not* getting
translated to `$N`, while the surrounding params do. The result is a SQL
string with mixed `$1, $2, :nas_ip::inet, $3` that PostgreSQL rejects with
``syntax error at or near ":"``.

Use ``CAST(:nas_ip AS inet)`` instead — the named param is then a clean
identifier (no trailing ``::``) and gets translated correctly.

The same trap applies to `:foo::uuid`, `:foo::jsonb`, `:foo::timestamp`,
etc. — any postgres typecast applied to a named parameter.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
GATEWAY_FEATURES = REPO_ROOT / "services" / "gateway" / "features"

# Match `:identifier::identifier` — the broken pattern.
# Allow `::identifier` not preceded by a `:name` (e.g. plain literals or
# expressions like `'10.0.0.1'::inet`, which the preprocessor leaves alone).
_BAD_PATTERN = re.compile(r":[a-zA-Z_][a-zA-Z0-9_]*::[a-zA-Z_]")


@pytest.mark.parametrize(
    "py_file",
    sorted(GATEWAY_FEATURES.glob("**/*.py")),
    ids=lambda p: p.relative_to(REPO_ROOT).as_posix(),
)
def test_no_named_param_double_colon_typecast(py_file: Path) -> None:
    """Each .py file under features/ must avoid `:foo::type` in SQL strings."""
    text = py_file.read_text(encoding="utf-8")
    matches = _BAD_PATTERN.findall(text)
    assert not matches, (
        f"{py_file.relative_to(REPO_ROOT).as_posix()} contains the broken "
        f"`:name::type` pattern: {matches}. "
        f"asyncpg's preprocessor mis-parses this; use `CAST(:name AS type)` "
        f"instead. See tests/unit/test_no_inline_inet_cast.py for context."
    )
