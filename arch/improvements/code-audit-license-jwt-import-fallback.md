# code-audit: License JWT verification silently bypassed when crypto deps unavailable (HIGH)

**Discovered**: 2026-05-22 code-audit overnight pass
**Severity**: HIGH

## Problem
`tessera/intelligence/license.py:123-161` `_verify_response` wraps the JWT verification in `try: import cryptography; import jose ... except ImportError: ...`. When `python-jose` or `cryptography` is not installed (base install, not `[intelligence]` extra), the code falls through and uses the raw unverified response body as authoritative. No warning is logged. A malicious license server (or MITM on a customer network) can return any `tier` claim and it's accepted.

Fail direction is open (accepting unverified claims) rather than conservative (downgrading to free on failure).

## Where
- File: `tessera/intelligence/license.py:123-161` (the `_verify_response` method)
- Specifically: the `except ImportError:` branch that returns the unverified response

## Suggested fix
On `ImportError` in `_verify_response`: log a `WARNING: license JWT verification skipped — cryptography/jose not installed; downgrading to free` and return a `LicenseStatus` with `tier="free"` and `verified=False`. Do not accept the unverified tier claim.

Alternative (stricter): require the `[intelligence]` extra explicitly. Raise `ConfigError` at proxy startup if `intelligence.enabled: true` but `cryptography`/`jose` are not importable. This would make the base install never reach this code path.

Add a `verified: bool` field on `LicenseStatus` to make the trust state explicit to downstream consumers.

## Effort
small (failure-mode change + log line + optional `verified` field)

## Acceptance criteria
- With `cryptography`/`jose` uninstalled, `_verify_response` returns `tier="free"` and logs a warning
- A regression test simulates `ImportError` and asserts the safe-default behavior
- Documentation update: README clearly states `tessera[intelligence]` is required when `intelligence.enabled: true`

## On merge
Folds into the OSS license/auth status doc. This file deleted after merge.
