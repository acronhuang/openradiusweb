#!/usr/bin/env python3
"""One-shot migration: encrypt existing plaintext radius_nas_clients.secret_encrypted.

After PR #72 ships:
  - New radius_nas_clients rows have AES-256-GCM ciphertext in secret_encrypted
  - Existing rows still have plaintext from before the migration
  - decrypt_secret() is permissive (returns input unchanged on unrecognised
    format) so legacy plaintext rows keep working — freeradius keeps signing
    RADIUS packets correctly during the migration window
  - But the on-disk DB still exposes the NAS shared secret if backups leak,
    so this script flips them to ciphertext

Walks every radius_nas_clients row, detects whether the
`secret_encrypted` column already contains ciphertext (via `is_encrypted()`),
and encrypts the plaintext rows in place.

Idempotent: re-running is safe — already-encrypted rows are skipped.

Usage (on the RADIUS server, with .env.production loaded so
ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT are in env):

    # Dry-run:
    sudo docker cp scripts/migrate_nas_secrets_to_encrypted.py orw-freeradius:/tmp/m.py
    sudo docker exec orw-freeradius python3 /tmp/m.py --dry-run

    # Real:
    sudo docker exec orw-freeradius python3 /tmp/m.py

(Run inside `orw-freeradius` container because that has python3-psycopg2
from apt + orw_common.secrets importable + the env vars set. Gateway
container uses asyncpg, no sync psycopg2.)

Exit codes:
  0  success (or no rows needed migration)
  1  no env vars / connection failure
  2  some rows failed to encrypt
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
            "freeradius_config_watcher).",
            file=sys.stderr,
        )
        return 1

    db_url = _build_db_url()
    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
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
                    "SELECT id, name, secret_encrypted FROM radius_nas_clients"
                )
                rows = cur.fetchall()

            print(f"Found {len(rows)} radius_nas_clients row(s) to inspect.")

            for row in rows:
                name = row["name"] or f"<id={row['id']}>"
                current = row["secret_encrypted"]
                if current is None or current == "":
                    null_or_empty += 1
                    continue
                if is_encrypted(current):
                    already += 1
                    continue

                if args.dry_run:
                    print(f"  WOULD encrypt: {name}")
                    encrypted += 1
                    continue

                try:
                    new_ct = encrypt_secret(current)
                except Exception as exc:  # pragma: no cover
                    failed.append(f"{name}: {exc}")
                    continue

                with conn.cursor() as wcur:
                    wcur.execute(
                        "UPDATE radius_nas_clients "
                        "SET secret_encrypted = %s "
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
