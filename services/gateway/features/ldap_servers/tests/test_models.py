"""Pydantic model validation for LDAPServerCreate / LDAPServerUpdate.

Locks in the contract: bind_dn and bind_password are REQUIRED on Create
(matches `bind_dn VARCHAR(500) NOT NULL` + `bind_password_encrypted TEXT
NOT NULL` in the schema). Catches the specific bug class where the API
accepted a blank bind_dn/password and then 500'd at INSERT time with a
NOT NULL violation — see PR #46.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from orw_common.models.ldap_server import LDAPServerCreate, LDAPServerUpdate


def _valid_create_kwargs() -> dict:
    return {
        "name": "AD-1",
        "host": "dc.example.com",
        "bind_dn": "CN=svc,DC=example,DC=com",
        "bind_password": "secret",
        "base_dn": "DC=example,DC=com",
    }


class TestCreateRequiresBindCredentials:
    def test_full_payload_validates(self):
        # Sanity check: the happy path still works after the field changes.
        m = LDAPServerCreate(**_valid_create_kwargs())
        assert m.bind_dn == "CN=svc,DC=example,DC=com"
        assert m.bind_password == "secret"

    def test_missing_bind_dn_rejected(self):
        kwargs = _valid_create_kwargs()
        del kwargs["bind_dn"]
        with pytest.raises(ValidationError) as exc:
            LDAPServerCreate(**kwargs)
        assert any(e["loc"] == ("bind_dn",) for e in exc.value.errors())

    def test_missing_bind_password_rejected(self):
        kwargs = _valid_create_kwargs()
        del kwargs["bind_password"]
        with pytest.raises(ValidationError) as exc:
            LDAPServerCreate(**kwargs)
        assert any(e["loc"] == ("bind_password",) for e in exc.value.errors())

    def test_blank_bind_dn_rejected(self):
        # Empty string was the other way the bug snuck through — DB still
        # rejects "" for VARCHAR NOT NULL? Actually it accepts empty strings;
        # but min_length=1 ensures we don't store useless empty credentials.
        kwargs = _valid_create_kwargs()
        kwargs["bind_dn"] = ""
        with pytest.raises(ValidationError):
            LDAPServerCreate(**kwargs)

    def test_blank_bind_password_rejected(self):
        kwargs = _valid_create_kwargs()
        kwargs["bind_password"] = ""
        with pytest.raises(ValidationError):
            LDAPServerCreate(**kwargs)


class TestUpdateAllowsOmitted:
    """Update is partial — bind_password omitted = "don't change"."""

    def test_no_bind_credentials_is_fine_on_update(self):
        # The Edit dialog leaves bind_password blank to mean "unchanged",
        # and that path must still be accepted post-fix.
        m = LDAPServerUpdate(name="AD-1-renamed")
        assert m.bind_dn is None
        assert m.bind_password is None
