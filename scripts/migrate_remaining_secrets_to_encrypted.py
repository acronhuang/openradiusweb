#!/usr/bin/env python3
"""One-shot migration: encrypt the 4 remaining plaintext *_encrypted columns.

After PR #74 ships, these tables get encrypt-on-write at the gateway
boundary, but existing rows remain plaintext until this migration runs:

    certificates.key_pem_encrypted              (TLS server private key)
    radius_realms.proxy_secret_encrypted        (RADIUS proxy secret)
    network_devices.snmp_community_encrypted    (SNMP community string)
    network_devices.coa_secret_encrypted        (CoA RADIUS secret)

Idempotent: re-running is safe — already-encrypted rows are skipped via
is_encrypted(). Each table has --dry-run by default — pass --apply to
actually write.

Usage (on the RADIUS server, with .env.production loaded so
ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT are in env):

    sudo docker cp scripts/migrate_remaining_secrets_to_encrypted.py \
        orw-freeradius:/tmp/m.py

    # Dry-run all 4 columns:
    sudo docker exec orw-freeradius python3 /tmp/m.py --dry-run

    # Real:
    sudo docker exec orw-freeradius python3 /tmp/m.py

(Run inside `orw-freeradius` container — has python3-psycopg2 from apt
+ orw_common.secrets importable + the env vars set. Gateway uses
asyncpg, no sync psycopg2.)

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


# (table, secret column, identifier column to print)
_TARGETS = [
    ("certificates", "key_pem_encrypted", "name"),
    ("radius_realms", "proxy_secret_encrypted", "name"),
    ("network_devices", "snmp_community_encrypted", "hostname"),
    ("network_devices", "coa_secret_encrypted", "hostname"),
]


def _build_db_url() -> str:
    if url := os.environ.get("ORW_DB_URL"):
        return url
    pw = os.environ.get("DB_PASSWORD")
    if not pw:
        print("ERROR: neither ORW_DB_URL nor DB_PASSWORD set.", file=sys.stderr)
        sys.exit(1)
    pw_quoted = urllib.parse.quote(pw, safe="")
    host = os.environ.get("DB_HOST", "postgres")
    return f"postgresql://orw:{pw_quoted}@{host}:5432/orw"


def _migrate_one(conn, table, column, ident_col, dry_run, encrypt_secret, is_encrypted):
    encrypted = already = null_or_empty = 0
    failed: list[str] = []

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, {ident_col}, {column} FROM {table}"
        )
        rows = cur.fetchall()

    print(f"\n--- {table}.{column} ({len(rows)} row(s)) ---")

    for row_id, ident, current in rows:
        label = ident or f"<id={row_id}>"
        if current is None or current == "":
            null_or_empty += 1
            continue
        if is_encrypted(current):
            already += 1
            continue

        if dry_run:
            print(f"  WOULD encrypt: {label}")
            encrypted += 1
            continue

        try:
            new_ct = encrypt_secret(current)
        except Exception as exc:  # pragma: no cover
            failed.append(f"{table}.{column} ({label}): {exc}")
            continue

        with conn.cursor() as wcur:
            wcur.execute(
                f"UPDATE {table} SET {column} = %s WHERE id = %s",
                (new_ct, row_id),
            )
        encrypted += 1
        print(f"  encrypted: {label}")

    verb = "WOULD encrypt" if dry_run else "Encrypted"
    print(f"  {verb}: {encrypted} | already encrypted: {already} | null/empty: {null_or_empty}")
    return encrypted, already, null_or_empty, failed


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
    except ImportError as exc:
        print(f"ERROR: psycopg2 missing — {exc}", file=sys.stderr)
        return 1
    try:
        from orw_common.secrets import encrypt_secret, is_encrypted
    except ImportError as exc:
        print(f"ERROR: orw_common.secrets missing — {exc}", file=sys.stderr)
        return 1

    db_url = _build_db_url()
    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        print(f"ERROR: cannot connect to postgres — {exc}", file=sys.stderr)
        return 1

    total_encrypted = total_already = total_null = 0
    all_failed: list[str] = []

    try:
        with conn:
            for table, column, ident_col in _TARGETS:
                e, a, n, f = _migrate_one(
                    conn, table, column, ident_col,
                    args.dry_run, encrypt_secret, is_encrypted,
                )
                total_encrypted += e
                total_already += a
                total_null += n
                all_failed.extend(f)
    finally:
        conn.close()

    print("\n" + "=" * 60)
    if args.dry_run:
        print(f"DRY RUN total: {total_encrypted} row(s) would be encrypted.")
    else:
        print(f"Encrypted total: {total_encrypted} row(s).")
    print(f"Already encrypted across all tables: {total_already}")
    print(f"Null/empty across all tables: {total_null}")
    if all_failed:
        print(f"FAILED: {len(all_failed)} row(s)")
        for line in all_failed:
            print(f"  - {line}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
