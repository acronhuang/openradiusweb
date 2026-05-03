#!/usr/bin/env python3
"""Rotate ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT.

Walks every encrypted column, decrypts each non-null value with the OLD
key set, re-encrypts with the NEW key set, writes back per row inside a
single transaction per table.

Designed for the maintenance-window flow described in
docs/runbook-key-rotation.md — services that hold the encryption key
must be stopped FIRST so they don't read mid-rotation rows that no
longer match their cached key.

Idempotent enough for emergencies: a row already on the new key will
fail to decrypt with the old key, which we surface as an error and skip
(use --skip-undecryptable to keep going). Re-running after a partial
failure picks up where we left off.

Usage (from /opt/openradiusweb on the prod host):

    sudo \
      ORW_SECRET_MASTER_OLD=<paste old master> \
      ORW_SECRET_KDF_SALT_OLD=<paste old salt> \
      ORW_SECRET_MASTER_NEW=<paste new master> \
      ORW_SECRET_KDF_SALT_NEW=<paste new salt> \
      DB_PASSWORD=<paste from .env.production> \
      python3 scripts/rotate_secret_master.py [--dry-run] [--skip-undecryptable]

The shell history will contain the old + new key material — clear it
or use a dedicated rotation shell. Don't pass keys as plain CLI args
(they show in `ps`); env vars are visible only to the process tree.

Exit codes:
    0  success
    1  bad config / missing env vars / db connection error
    2  one or more rows failed to rotate (script prints which)
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse


# (table, column) pairs encrypted with ORW_SECRET_MASTER. Mirrors the
# information_schema query in docs/runbook-post-deploy-verification.md
# step 5; if a future PR adds a 7th column, add it here too AND extend
# the post-deploy runbook in the same PR.
ENCRYPTED_COLUMNS: list[tuple[str, str]] = [
    ("ldap_servers", "bind_password_encrypted"),
    ("radius_nas_clients", "secret_encrypted"),
    ("certificates", "key_pem_encrypted"),
    ("radius_realms", "proxy_secret_encrypted"),
    ("network_devices", "snmp_community_encrypted"),
    ("network_devices", "coa_secret_encrypted"),
]


def _build_db_url() -> str:
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


def _build_vaults():
    """Build two _Vault instances — one for the old key set, one for the
    new — without polluting the global module-level vault. Uses internals
    deliberately because the public API doesn't take key material as a
    parameter (by design — the production key only comes from env)."""
    try:
        from orw_common.secrets import (  # type: ignore
            _Vault, _ENV_MASTER, _ENV_SALT,
        )
    except ImportError as exc:
        print(
            f"ERROR: orw_common.secrets missing — {exc}\n"
            "Run inside a container that has it (gateway / freeradius / "
            "freeradius_config_watcher), or add shared/ to PYTHONPATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    required = {
        "ORW_SECRET_MASTER_OLD", "ORW_SECRET_KDF_SALT_OLD",
        "ORW_SECRET_MASTER_NEW", "ORW_SECRET_KDF_SALT_NEW",
    }
    missing = required - set(os.environ)
    if missing:
        print(
            f"ERROR: missing env var(s): {', '.join(sorted(missing))}.\n"
            "See script docstring for the full invocation.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _vault_with(master: str, salt: str):
        # Save + restore the module-global env so each derive_key picks
        # up the right values. This is single-threaded; OK to mutate.
        prev_master = os.environ.get(_ENV_MASTER)
        prev_salt = os.environ.get(_ENV_SALT)
        os.environ[_ENV_MASTER] = master
        os.environ[_ENV_SALT] = salt
        try:
            v = _Vault()
            v._aesgcm()  # force key derivation now (Argon2id ~100ms)
            return v
        finally:
            if prev_master is None:
                os.environ.pop(_ENV_MASTER, None)
            else:
                os.environ[_ENV_MASTER] = prev_master
            if prev_salt is None:
                os.environ.pop(_ENV_SALT, None)
            else:
                os.environ[_ENV_SALT] = prev_salt

    old = _vault_with(
        os.environ["ORW_SECRET_MASTER_OLD"],
        os.environ["ORW_SECRET_KDF_SALT_OLD"],
    )
    new = _vault_with(
        os.environ["ORW_SECRET_MASTER_NEW"],
        os.environ["ORW_SECRET_KDF_SALT_NEW"],
    )
    return old, new


def _rotate_table(
    conn, table: str, column: str, old, new, *,
    dry_run: bool, skip_undecryptable: bool,
) -> tuple[int, int, list[str]]:
    """Returns (rotated, skipped, failures)."""
    import psycopg2.extras

    rotated = 0
    skipped = 0
    failures: list[str] = []

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT id, {column} AS val FROM {table}")
        rows = cur.fetchall()

    print(f"\n=== {table}.{column} — {len(rows)} row(s) ===")

    for row in rows:
        rid = row["id"]
        ct_old = row["val"]
        if ct_old is None or ct_old == "":
            skipped += 1
            continue

        try:
            plaintext = old.decrypt(ct_old)
        except Exception as exc:
            msg = (
                f"  id={rid}: decrypt with OLD key failed ({type(exc).__name__}): "
                f"{exc}"
            )
            if skip_undecryptable:
                print(msg + "  [skipped]", file=sys.stderr)
                skipped += 1
                continue
            failures.append(f"{table}.{column} id={rid}: {exc}")
            print(msg, file=sys.stderr)
            continue

        if plaintext is None:
            skipped += 1
            continue

        try:
            ct_new = new.encrypt(plaintext)
        except Exception as exc:
            failures.append(f"{table}.{column} id={rid}: re-encrypt failed: {exc}")
            print(
                f"  id={rid}: re-encrypt with NEW key failed: {exc}",
                file=sys.stderr,
            )
            continue

        if dry_run:
            print(f"  id={rid}: WOULD rotate (old len={len(ct_old)} -> new len={len(ct_new)})")
            rotated += 1
            continue

        with conn.cursor() as wcur:
            wcur.execute(
                f"UPDATE {table} SET {column} = %s WHERE id = %s",
                (ct_new, rid),
            )
        rotated += 1
        print(f"  id={rid}: rotated")

    return rotated, skipped, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Decrypt + re-encrypt in memory; don't write back.",
    )
    parser.add_argument(
        "--skip-undecryptable", action="store_true",
        help="Skip rows that fail to decrypt with the OLD key (assumes "
             "they're already on the NEW key from a previous partial "
             "rotation). Otherwise fail loudly.",
    )
    args = parser.parse_args()

    try:
        import psycopg2
    except ImportError as exc:
        print(f"ERROR: psycopg2 missing — {exc}", file=sys.stderr)
        return 1

    old, new = _build_vaults()
    db_url = _build_db_url()
    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as exc:
        from orw_common.db_url_safe import format_db_error  # type: ignore
        print(
            f"ERROR: cannot connect to postgres — {format_db_error(exc, db_url)}",
            file=sys.stderr,
        )
        return 1

    total_rotated = total_skipped = 0
    all_failures: list[str] = []
    try:
        # One transaction per table — if mid-table fails we still commit
        # the rows we managed up to that point, so re-running with
        # --skip-undecryptable picks up from there.
        for table, column in ENCRYPTED_COLUMNS:
            with conn:
                rotated, skipped, failures = _rotate_table(
                    conn, table, column, old, new,
                    dry_run=args.dry_run,
                    skip_undecryptable=args.skip_undecryptable,
                )
            total_rotated += rotated
            total_skipped += skipped
            all_failures.extend(failures)
    finally:
        conn.close()

    print()
    print("=" * 60)
    if args.dry_run:
        print(f"DRY RUN — would have rotated {total_rotated} row(s).")
    else:
        print(f"Rotated {total_rotated} row(s).")
    print(f"Skipped (null/empty/already-new): {total_skipped}")
    if all_failures:
        print(f"\nFAILED: {len(all_failures)} row(s)")
        for line in all_failures:
            print(f"  - {line}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
