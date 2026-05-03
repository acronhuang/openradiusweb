"""Helpers to safely log DB connection strings + errors.

The problem: code that does `psycopg2.connect(db_url)` followed by
`except Exception as e: log.error("connect failed: %s — url=%s", e, db_url)`
leaks the password to the log on every connect failure. Same for
asyncpg / sqlalchemy. The exception message itself usually doesn't
include the password (psycopg2's `OperationalError` carries hostname
+ port + reason, not the DSN), but the moment a developer adds the
URL to the log line for "context", the secret leaks.

This module centralises:

  mask_db_url(url)        -> URL with password replaced by `***`
  format_db_error(err, url)
                           -> "<error> [url=<masked>]" — drop-in
                              replacement for f"{err} url={url}"

Use these instead of raw f-strings any time a DB URL or its
exception is going to a log / stderr / API response.
"""
from __future__ import annotations

import re
from typing import Optional


# Matches the password portion of:
#   postgresql://user:PASS@host:port/db
#   postgresql+asyncpg://user:PASS@host/db
#   postgres://user:PASS@host/db
#   ldap://user:PASS@host/...
# Captures everything between `://user:` and the next `@`.
_DB_URL_PASSWORD_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+\-.]*://"
    r"[^:/?#@\s]+:)"
    r"(?P<password>[^@\s]+)"
    r"(?P<at>@)"
)


def mask_db_url(url: Optional[str]) -> Optional[str]:
    """Return `url` with its password component replaced by `***`.

    Idempotent — calling on an already-masked URL returns it unchanged.
    Returns input unchanged for None / empty / passwordless URLs.

    Examples:
        >>> mask_db_url("postgresql://orw:hunter2@postgres:5432/orw")
        'postgresql://orw:***@postgres:5432/orw'
        >>> mask_db_url("postgresql+asyncpg://u:p%40w@h/db")
        'postgresql+asyncpg://u:***@h/db'
        >>> mask_db_url(None) is None
        True
        >>> mask_db_url("postgresql://h/db")     # no auth at all
        'postgresql://h/db'
        >>> mask_db_url("postgresql://u@h/db")   # user but no password
        'postgresql://u@h/db'
    """
    if not url:
        return url
    return _DB_URL_PASSWORD_RE.sub(
        lambda m: f"{m.group('scheme')}***{m.group('at')}",
        url,
    )


def format_db_error(err: BaseException, url: Optional[str] = None) -> str:
    """Format a DB-related exception for safe logging.

    Always includes the exception message; optionally appends the
    masked URL for context. Drop-in replacement for the unsafe
    `f"connect failed: {err} url={url}"`.

    Examples:
        >>> import psycopg2
        >>> try:
        ...     raise psycopg2.OperationalError("connection refused")
        ... except Exception as e:
        ...     format_db_error(e, "postgresql://u:secret@h/db")
        'connection refused [url=postgresql://u:***@h/db]'

        >>> format_db_error(RuntimeError("oops"))
        'oops'
    """
    msg = str(err) or err.__class__.__name__
    if url is None:
        return msg
    return f"{msg} [url={mask_db_url(url)}]"


def scrub_message(text: str) -> str:
    """Best-effort scrub of any DB URLs found inside a free-form string.

    Use when you have a log line that might contain a DB URL but you
    can't easily extract it (e.g. an unstructured exception with the
    URL embedded in the middle of a sentence). Slower than
    `mask_db_url` because it scans the whole string.
    """
    if not text:
        return text
    return _DB_URL_PASSWORD_RE.sub(
        lambda m: f"{m.group('scheme')}***{m.group('at')}",
        text,
    )
