#!/usr/bin/env python3
"""Lint check: writes to *_encrypted columns must go through encrypt_secret().

Background: PRs #70-#74 introduced AES-256-GCM at-rest encryption for
six DB columns suffixed `_encrypted`. The naming alone is not a guard —
nothing stops a future PR from `INSERT INTO ... (secret_encrypted) VALUES
(:secret)` and binding the *plaintext* value, silently re-introducing
the very leak the encryption was added to fix (see
docs/security-audit-2026-05-02-secret-storage.md).

This hook scans staged Python files for SQL strings that *write* to a
`*_encrypted` column (INSERT INTO, UPDATE ... SET, RETURNING in
combination with INSERT/UPDATE) and fails if the same file does not
also import `encrypt_secret` from `orw_common.secrets`.

It does not check SELECT-only files because decryption happens
transparently in the repository layer; downstream service/route code
consumes already-decrypted values.

False positives can be silenced inline with `# orw-lint: skip-encryption-check`
on the same line as the SQL — use sparingly and only after thinking
about whether the value really doesn't need encrypting.

Exit codes:
    0  No violations.
    1  At least one file writes to a `*_encrypted` column without
       importing the encryption helper.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Files that legitimately mention `*_encrypted` columns in SQL contexts
# without using encrypt_secret (e.g. they ARE the helper, or they're
# tests that mock the encryption boundary).
ALLOWLIST: frozenset[str] = frozenset({
    "shared/orw_common/secrets.py",
    "services/gateway/utils/safe_sql.py",
    "services/gateway/tests/unit/test_safe_sql.py",
    "services/gateway/tests/unit/test_secrets.py",
    "services/gateway/tests/contracts/test_schema_model_alignment.py",
    "scripts/check_encrypted_columns_wrapped.py",
    "scripts/migrate_ldap_passwords_to_encrypted.py",
    "scripts/migrate_nas_secrets_to_encrypted.py",
    "scripts/migrate_remaining_secrets_to_encrypted.py",
})

# Matches an SQL string that writes to a column ending in `_encrypted`.
# We apply these to *individual* string literals from the AST, so they
# don't span across unrelated statements concatenated in the same file.
WRITE_PATTERNS = [
    re.compile(
        r"INSERT\s+INTO\s+\w+\b.*?\b\w+_encrypted\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"UPDATE\s+\w+\b.*?\bSET\b.*?\b\w+_encrypted\b",
        re.IGNORECASE | re.DOTALL,
    ),
]

SKIP_MARKER = "orw-lint: skip-encryption-check"

HELPER_IMPORT = re.compile(
    r"from\s+orw_common\.secrets\s+import\s+[^\n]*\bencrypt_secret\b"
)


def _string_constants(source: str) -> list[tuple[int, str]]:
    """Yield (lineno, value) for every string literal in a Python file.
    Uses AST so implicit concatenation (`"a" "b"`) and triple-quoted
    blocks each become one logical string."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            # f-string: stitch the literal parts together (FormattedValue
            # interpolations are dropped — we only need to spot column
            # names that show up as plain text inside the SQL).
            buf = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    buf.append(v.value)
            if buf:
                out.append((node.lineno, "".join(buf)))
    return out


def _line_has_skip(source_lines: list[str], lineno: int) -> bool:
    """Pragma applies if it appears on the start line of the SQL string
    or any of the next 5 lines — covers cases where the marker sits on a
    closing paren of a multi-line text(...) call."""
    for i in range(lineno - 1, min(lineno + 5, len(source_lines))):
        if SKIP_MARKER in source_lines[i]:
            return True
    return False


def _check_file(path: Path, repo_root: Path) -> list[str]:
    rel = path.relative_to(repo_root).as_posix()
    if rel in ALLOWLIST:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for lineno, s in _string_constants(text):
        for pat in WRITE_PATTERNS:
            m = pat.search(s)
            if not m:
                continue
            if _line_has_skip(source_lines, lineno):
                continue
            hits.append((lineno, m.group(0)[:120].replace("\n", " ")))
            break

    if not hits:
        return []
    if HELPER_IMPORT.search(text):
        return []

    return [
        f"{rel}: writes a *_encrypted column without importing "
        f"encrypt_secret from orw_common.secrets",
        *(f"    line {ln}: {snippet}" for ln, snippet in hits[:3]),
    ]


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent

    # When called by pre-commit, staged files are passed in argv. When
    # called manually (`pre-commit run --all-files` or this script
    # directly), fall back to scanning every .py under services/ + scripts/.
    if argv:
        paths = [Path(p).resolve() for p in argv if p.endswith(".py")]
    else:
        paths = [
            *(repo_root / "services").rglob("*.py"),
            *(repo_root / "scripts").rglob("*.py"),
            *(repo_root / "shared").rglob("*.py"),
        ]

    failed = False
    for p in paths:
        if not p.exists():
            continue
        try:
            p.relative_to(repo_root)
        except ValueError:
            continue
        msgs = _check_file(p, repo_root)
        if msgs:
            failed = True
            for m in msgs:
                print(m, file=sys.stderr)

    if failed:
        print(
            "\nIf the value really should bypass encryption (e.g. you're "
            "writing pre-encrypted bytes from a migration script), add "
            f"`# {SKIP_MARKER}` on the SQL line and explain in a code "
            "comment why.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
