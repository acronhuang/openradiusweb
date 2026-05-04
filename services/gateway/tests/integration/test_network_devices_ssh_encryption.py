"""Integration test: insert a network_device with SSH credentials,
verify the password is AES-256-GCM ciphertext on disk + decrypts to
the original plaintext. Mirrors the PR #74 verification pattern for
snmp_community + coa_secret, applied to PR #100's new ssh_password
column.

Real Postgres via testcontainers fixture. Catches the SQL bug class
PRs #94 + #97 caught (asyncpg parameter type coercion, missing
columns in INSERT, etc.).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from features.network_devices import repository as repo
from orw_common.secrets import decrypt_secret


@pytest.mark.asyncio
async def test_ssh_credentials_encrypted_on_disk_and_decrypt_round_trip(
    db_session, tenant_id,
):
    PLAINTEXT_PW = "switch-pw-with-symbols-!@#$%^&*"
    USERNAME = "netadmin"

    inserted = await repo.insert_network_device(
        db_session,
        tenant_id=tenant_id,
        ip_address="10.0.0.42",
        hostname="lab-switch-1",
        vendor="cisco",
        model="C9300",
        os_version="16.12",
        device_type="switch",
        management_protocol="ssh",
        snmp_version=None,
        snmp_community=None,
        ssh_username=USERNAME,
        ssh_password=PLAINTEXT_PW,
        poll_interval_seconds=300,
    )
    device_id = inserted["id"]

    # Re-read raw columns from disk (bypass any service-layer decrypt)
    result = await db_session.execute(
        text(
            "SELECT ssh_username, ssh_password_encrypted "
            "FROM network_devices WHERE id = :id"
        ),
        {"id": device_id},
    )
    row = result.first()
    assert row is not None
    on_disk_user, on_disk_pw = row

    # Username is plaintext per design (low sensitivity)
    assert on_disk_user == USERNAME

    # Password column must NOT contain the plaintext
    assert on_disk_pw is not None
    assert PLAINTEXT_PW not in on_disk_pw

    # Ciphertext shape: base64-encoded blob, version byte 0x01 → 'A' prefix
    assert on_disk_pw.startswith("A")
    assert len(on_disk_pw) >= 28  # version + 12-byte nonce + tag, base64'd

    # Round-trip: orw_common.secrets.decrypt_secret returns original
    assert decrypt_secret(on_disk_pw) == PLAINTEXT_PW


@pytest.mark.asyncio
async def test_null_ssh_password_stored_as_null_not_empty_string(
    db_session, tenant_id,
):
    """SNMP-only device (no SSH creds) → both columns NULL, not the
    string "None" or empty-encrypted-blob."""
    inserted = await repo.insert_network_device(
        db_session,
        tenant_id=tenant_id,
        ip_address="10.0.0.43",
        hostname="snmp-only",
        vendor="cisco",
        model=None,
        os_version=None,
        device_type="switch",
        management_protocol="snmp",
        snmp_version="v2c",
        snmp_community="public",  # encrypted same way
        ssh_username=None,
        ssh_password=None,
        poll_interval_seconds=300,
    )
    result = await db_session.execute(
        text(
            "SELECT ssh_username, ssh_password_encrypted "
            "FROM network_devices WHERE id = :id"
        ),
        {"id": inserted["id"]},
    )
    on_disk_user, on_disk_pw = result.first()
    assert on_disk_user is None
    assert on_disk_pw is None
