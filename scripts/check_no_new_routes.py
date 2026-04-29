#!/usr/bin/env python3
"""Lint check: prevent new files in services/gateway/routes/.

Per development-manual.md §10.6.3, the flat routes/ layout is transitional.
New features must use services/gateway/features/<name>/. This script enforces
that by snapshotting the legacy file set and failing if anything new appears.

When a legacy file is migrated and deleted, remove its entry from
LEGACY_ROUTES below.

Exit codes:
    0  No violations.
    1  New file(s) found in routes/, OR an entry in LEGACY_ROUTES was deleted
       without being removed from this script.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Snapshot of legacy route files as of 2026-04-28 (manual v1.3).
# Remove an entry when the file has been migrated and deleted.
# See docs/migration-features.md for the human-readable tracker.
LEGACY_ROUTES: frozenset[str] = frozenset({
    "certificates.py",
})

ROUTES_DIR = Path("services/gateway/routes")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    routes_dir = repo_root / ROUTES_DIR

    if not routes_dir.is_dir():
        # Routes dir fully removed — migration complete.
        if LEGACY_ROUTES:
            print(
                f"ERROR: {ROUTES_DIR} no longer exists, but LEGACY_ROUTES "
                f"still lists {len(LEGACY_ROUTES)} file(s). "
                f"Empty LEGACY_ROUTES in {Path(__file__).name}.",
                file=sys.stderr,
            )
            return 1
        return 0

    present = {
        p.name
        for p in routes_dir.iterdir()
        if p.is_file() and p.suffix == ".py" and p.name != "__init__.py"
    }

    new_files = present - LEGACY_ROUTES
    deleted_files = LEGACY_ROUTES - present

    failed = False

    if new_files:
        failed = True
        print(
            "ERROR: New file(s) added to services/gateway/routes/ — this "
            "directory is frozen.",
            file=sys.stderr,
        )
        for name in sorted(new_files):
            print(f"  - {ROUTES_DIR.as_posix()}/{name}", file=sys.stderr)
        print(
            "\nNew features must live under services/gateway/features/<name>/.\n"
            "See docs/development-manual.md §10.6 for the standard layout.",
            file=sys.stderr,
        )

    if deleted_files:
        failed = True
        print(
            "\nERROR: Entries in LEGACY_ROUTES no longer exist on disk — they "
            "appear migrated but the snapshot was not updated.",
            file=sys.stderr,
        )
        for name in sorted(deleted_files):
            print(f"  - {name}", file=sys.stderr)
        print(
            f"\nRemove these entries from LEGACY_ROUTES in {Path(__file__).name} "
            f"and update docs/migration-features.md.",
            file=sys.stderr,
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
