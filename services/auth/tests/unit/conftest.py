"""Unit test fixtures for the freeradius_config_manager + watcher tests.

Adds the repo's `shared/` directory to sys.path so `orw_common.secrets`
(and friends) resolves at import time. The freeradius_config_manager
imports `decrypt_secret` at module load, so this needs to run before
the manager is imported.

Also stubs out the encryption env vars so `_derive_key()` doesn't blow
up. The test bodies use plaintext-passthrough secrets via the
permissive-decrypt path (legacy plaintext returns unchanged), so the
actual key value doesn't matter — but the env vars must exist.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHARED = _REPO_ROOT / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


# Stub encryption env so orw_common.secrets._derive_key() succeeds.
# Doesn't need to match production — these tests don't decrypt real data.
os.environ.setdefault(
    "ORW_SECRET_MASTER",
    "unit-test-master-not-for-prod-use-anywhere",
)
os.environ.setdefault(
    "ORW_SECRET_KDF_SALT",
    base64.urlsafe_b64encode(b"unit-test-16byte").rstrip(b"=").decode("ascii"),
)
