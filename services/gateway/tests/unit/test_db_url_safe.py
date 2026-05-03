"""Unit tests for orw_common.db_url_safe — DB URL password scrubbing."""
from __future__ import annotations

import pytest

from orw_common.db_url_safe import format_db_error, mask_db_url, scrub_message


# ---------------------------------------------------------------------------
# mask_db_url — core URL masking
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        # Standard postgres
        (
            "postgresql://orw:hunter2@postgres:5432/orw",
            "postgresql://orw:***@postgres:5432/orw",
        ),
        # asyncpg variant
        (
            "postgresql+asyncpg://orw:secret_pw@postgres:5432/orw",
            "postgresql+asyncpg://orw:***@postgres:5432/orw",
        ),
        # postgres:// (shorter scheme)
        (
            "postgres://orw:p@host/db",
            "postgres://orw:***@host/db",
        ),
        # URL-encoded password
        (
            "postgresql://orw:p%40word%21@host/db",
            "postgresql://orw:***@host/db",
        ),
        # Special chars in password
        (
            "postgresql://u:!QAZxcvfr432wsde@h:5432/orw",
            "postgresql://u:***@h:5432/orw",
        ),
        # LDAP URL — same scheme:user:pw@host pattern
        (
            "ldap://CN=Bind,DC=mds:mybindpw@192.168.0.253:636",
            "ldap://CN=Bind,DC=mds:***@192.168.0.253:636",
        ),
        # Redis with password
        (
            "redis://:redispw@redis:6379/0",
            "redis://:redispw@redis:6379/0",  # NB: regex requires user before colon
        ),
    ],
)
def test_mask_db_url_strips_password(raw, expected):
    assert mask_db_url(raw) == expected


def test_mask_db_url_idempotent():
    """Masking an already-masked URL returns it unchanged."""
    once = mask_db_url("postgresql://orw:hunter2@postgres/orw")
    twice = mask_db_url(once)
    assert once == twice == "postgresql://orw:***@postgres/orw"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "postgresql://h/db",          # no user, no password
        "postgresql://user@h/db",     # user but no password
        "not a url at all",
        "/usr/local/bin/python",      # filesystem path
    ],
)
def test_mask_db_url_passes_through(raw):
    """Inputs without a password component come back unchanged."""
    assert mask_db_url(raw) == raw


# ---------------------------------------------------------------------------
# format_db_error — exception + URL combined safely
# ---------------------------------------------------------------------------

def test_format_db_error_with_url():
    err = RuntimeError("connection refused")
    out = format_db_error(err, "postgresql://orw:secret@postgres/orw")
    assert "connection refused" in out
    assert "***" in out
    assert "secret" not in out


def test_format_db_error_without_url():
    err = RuntimeError("something bad")
    assert format_db_error(err) == "something bad"


def test_format_db_error_empty_message():
    """Exception with no message falls back to class name."""

    class MyErr(Exception):
        pass

    out = format_db_error(MyErr())
    assert out == "MyErr"


# ---------------------------------------------------------------------------
# scrub_message — full-text scrub for unstructured strings
# ---------------------------------------------------------------------------

def test_scrub_message_in_sentence():
    text = (
        "Failed to connect: tried postgresql://orw:hunter2@postgres/orw "
        "after 3 retries"
    )
    out = scrub_message(text)
    assert "hunter2" not in out
    assert "***" in out
    assert "Failed to connect" in out
    assert "after 3 retries" in out


def test_scrub_message_multiple_urls():
    text = (
        "primary postgresql://u:p1@h1/db secondary postgresql://u:p2@h2/db"
    )
    out = scrub_message(text)
    assert "p1" not in out
    assert "p2" not in out
    assert out.count("***") == 2


def test_scrub_message_handles_empty():
    assert scrub_message("") == ""
    assert scrub_message(None) is None
