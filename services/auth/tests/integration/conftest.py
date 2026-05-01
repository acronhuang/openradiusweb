"""Integration test fixtures for the freeradius config templates.

Renders Jinja2 templates from `services/auth/freeradius/templates/`,
writes them into a tmp dir mimicking /etc/freeradius layout, then runs
`freeradius -CX` in a stock freeradius container against those configs.

Catches the PR #36/#38/#39 bug class:
  - PR #36: `preprocess` referenced in 'accounting' section (illegal —
    preprocess only exposes authorize/pre-proxy methods)
  - PR #38: `remove_reply_message_authenticator` referenced as a module
    but not actually present in mods-enabled
  - PR #39: any other module/policy reference in templates that doesn't
    resolve in the running freeradius config

Auto-skips when Docker isn't available (or ORW_SKIP_INTEGRATION=1).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE_DIR = REPO_ROOT / "services" / "auth" / "freeradius" / "templates"

# Same image as prod (Dockerfile.freeradius FROM line). Stock freeradius
# without rlm_python3 — that's fine because Phase 3 tests render with
# has_python=False (the path the prod manager takes when rlm_python3
# isn't bundled).
FREERADIUS_IMAGE = os.environ.get(
    "ORW_TEST_FREERADIUS_IMAGE", "freeradius/freeradius-server:3.2.3"
)


# ---------------------------------------------------------------------------
# Skip-if-no-Docker gate
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    if os.environ.get("ORW_SKIP_INTEGRATION"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_DOCKER_OK = _docker_available()
_SKIP_REASON = (
    "Docker not available (or ORW_SKIP_INTEGRATION set). "
    "Skipping freeradius -CX integration tests."
)


def pytest_collection_modifyitems(config, items):
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
# Jinja env (matches the manager's settings)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


# ---------------------------------------------------------------------------
# Helper: run freeradius -CX against a directory of rendered configs
# ---------------------------------------------------------------------------

def _to_docker_mount(host_path: str) -> str:
    """Translate Windows tmp path to a Docker-compatible mount source.

    `C:\\Users\\foo` -> `/c/Users/foo`. On Linux/Mac this is a no-op.
    """
    p = host_path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


@pytest.fixture
def freeradius_check() -> Iterator:
    """Yields a callable: check(rendered: dict[str, str]) -> CheckResult.

    `rendered` is a mapping of relative path under /etc/freeradius/ to the
    file contents. E.g. {"sites-enabled/default": "...", "clients.conf": "..."}

    The callable copies these files over the stock freeradius config and
    runs `freeradius -CX`. Returns a CheckResult with exit_code, stdout,
    stderr — pytest assertions live in the test, not the fixture.
    """
    tmp_dirs = []

    class CheckResult:
        def __init__(self, exit_code: int, stdout: str, stderr: str):
            self.exit_code = exit_code
            self.stdout = stdout
            self.stderr = stderr

        @property
        def ok(self) -> bool:
            return self.exit_code == 0

        def error_lines(self) -> list[str]:
            """Filter to obvious error lines for clearer pytest failures."""
            return [
                line for line in self.stdout.splitlines()
                if "error" in line.lower()
                or "rlm_" in line.lower() and "fail" in line.lower()
            ]

    def check(rendered: dict[str, str]) -> CheckResult:
        tmp = tempfile.mkdtemp(prefix="orw-freeradius-test-")
        tmp_dirs.append(tmp)
        # Write each rendered file into the tmp dir (mirroring /etc/freeradius layout)
        for rel_path, content in rendered.items():
            target = os.path.join(tmp, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            # newline="\n": freeradius parses CRLF as the rest-of-line, so
            # writing CRLF on Windows breaks every config it reads.
            with open(target, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)

        # Build a one-shot bash command: copy each file into /etc/freeradius
        # then run freeradius -CX. set -e ensures any cp failure bubbles up.
        copy_cmds = " && ".join(
            f"cp /orw-test/{rel} /etc/freeradius/{rel}"
            for rel in rendered.keys()
        )
        bash_cmd = f"set -e; {copy_cmds} && freeradius -CX"

        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{_to_docker_mount(tmp)}:/orw-test:ro",
                FREERADIUS_IMAGE,
                "bash", "-c", bash_cmd,
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        return CheckResult(result.returncode, result.stdout, result.stderr)

    yield check

    for d in tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)
