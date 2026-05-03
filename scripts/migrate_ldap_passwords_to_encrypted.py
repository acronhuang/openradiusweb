#!/usr/bin/env python3
"""One-shot migration: encrypt existing plaintext bind_password_encrypted rows.

After PR #71 ships:
  - New ldap_servers rows have AES-256-GCM ciphertext in bind_password_encrypted
  - Existing rows still have plaintext from before the migration

Historical note: between PR #71 and the strict-mode flip, decrypt_secret()
returned unrecognised input unchanged so legacy plaintext kept working
during the migration window. That fallback was removed once every
production row was verified as ciphertext. If you're running this
script on an environment that's still on the permissive build, the
encryption step still works the same way.

This script walks every ldap_servers row, detects whether the
`bind_password_encrypted` column already contains ciphertext (via
`is_encrypted()`), and encrypts the plaintext rows in place.

Idempotent: re-running is safe — already-encrypted rows are skipped.

Usage (run on the RADIUS server, with .env.production loaded so
ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT are in env):

    # Dry-run (counts only, no writes):
    python3 scripts/migrate_ldap_passwords_to_encrypted.py --dry-run

    # Actual migration:
    python3 scripts/migrate_ldap_passwords_to_encrypted.py

    # OR via docker exec into the gateway container, which already has
    # the env vars + orw_common importable:
    sudo docker compose exec gateway python /app/scripts/migrate_ldap_passwords_to_encrypted.py

Requires:
  - postgres reachable via DB_PASSWORD env (or ORW_DB_URL override)
  - cryptography + argon2-cffi installed (see services/gateway/requirements.txt)

Exit codes:
  0  success (or no rows needed migration)
  1  no env vars / connection failure
  2  some rows failed to encrypt (output names which)
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse


def _build_db_url() -> str:
    """Resolve postgres URL from env. Try ORW_DB_URL first (freeradius
    convention), then construct from DB_PASSWORD (compose convention)."""
    if url := os.environ.get("ORW_DB_URL"):
        return url
    pw = os.environ.get("DB_PASSWORD")
    if not pw:
        print(
            "ERROR: neither ORW_DB_URL nor DB_PASSWORD set. "
            "Source .env.production first.",
            file=sys.stderr,
        )
        sys.exit(1)
    pw_quoted = urllib.parse.quote(pw, safe="")
    host = os.environ.get("DB_HOST", "postgres")
    return f"postgresql://orw:{pw_quoted}@{host}:5432/orw"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would be migrated, don't write.",
    )
    args = parser.parse_args()

    # Imports here so --help works without env vars set
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        print(f"ERROR: psycopg2 missing — {exc}", file=sys.stderr)
        return 1
    try:
        from orw_common.secrets import encrypt_secret, is_encrypted
    except ImportError as exc:
        print(
            f"ERROR: orw_common.secrets missing — {exc}\n"
            "Run inside a container that has it (gateway / freeradius / "
            "freeradius_config_watcher), or add shared/ to PYTHONPATH.",
            file=sys.stderr,
        )
        return 1

    db_url = _build_db_url()
    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        # Use orw_common.db_url_safe to mask the password if the URL ends
        # up in the error log via str(exc) or context.
        from orw_common.db_url_safe import format_db_error
        print(
            f"ERROR: cannot connect to postgres — {format_db_error(exc, db_url)}",
            file=sys.stderr,
        )
        return 1

    failed: list[str] = []
    encrypted = 0
    already = 0
    null_or_empty = 0

    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, name, bind_password_encrypted FROM ldap_servers"
                )
                rows = cur.fetchall()

            print(f"Found {len(rows)} ldap_servers row(s) to inspect.")

            for row in rows:
                name = row["name"] or f"<id={row['id']}>"
                current = row["bind_password_encrypted"]
                if current is None or current == "":
                    null_or_empty += 1
                    continue
                if is_encrypted(current):
                    already += 1
                    continue

                # Plaintext row — encrypt it.
                if args.dry_run:
                    print(f"  WOULD encrypt: {name}")
                    encrypted += 1
                    continue

                try:
                    new_ct = encrypt_secret(current)
                except Exception as exc:  # pragma: no cover (encrypt rarely fails)
                    failed.append(f"{name}: {exc}")
                    continue

                with conn.cursor() as wcur:
                    wcur.execute(
                        "UPDATE ldap_servers "
                        "SET bind_password_encrypted = %s "
                        "WHERE id = %s",
                        (new_ct, row["id"]),
                    )
                encrypted += 1
                print(f"  encrypted: {name}")

    finally:
        conn.close()

    print()
    print("=" * 60)
    if args.dry_run:
        print(f"DRY RUN — would have encrypted {encrypted} row(s).")
    else:
        print(f"Encrypted {encrypted} row(s).")
    print(f"Already encrypted (skipped): {already}")
    print(f"Null/empty (skipped): {null_or_empty}")
    if failed:
        print(f"FAILED: {len(failed)} row(s)")
        for line in failed:
            print(f"  - {line}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
