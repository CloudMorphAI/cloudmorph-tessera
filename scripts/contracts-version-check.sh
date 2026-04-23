#!/usr/bin/env bash
# scripts/contracts-version-check.sh
#
# Enforce that any change to contracts/*.schema.json is accompanied by
# a schemaVersion bump per the matrix in
# status/contracts/02_contracts_audit.md §2.6:
#
#   * Add optional field          → minor (v0.1 → v0.2)
#   * Add required field          → major (v0.1 → v1.0)
#   * Remove or rename field      → major
#   * Tighten enum/pattern        → major
#   * Loosen enum/pattern         → minor
#   * Bug fix (no semantic chg)   → patch (rare; usually means doc-only edit)
#
# This is a coarse heuristic: we detect whether `schemaVersion` field changed
# and whether the schema body changed. Anything where the body changed but
# the version didn't bump is a CI fail. Bump category enforcement is
# left to PR review — this script only catches the no-bump case.

set -euo pipefail

BASE_REF="${BASE_REF:-origin/main}"

# Allow running locally with no remote — fall back to HEAD~1
if ! git rev-parse --verify "$BASE_REF" > /dev/null 2>&1; then
  BASE_REF="HEAD~1"
  if ! git rev-parse --verify "$BASE_REF" > /dev/null 2>&1; then
    echo "✓ contracts-version-check: no base ref to compare; skipping"
    exit 0
  fi
fi

CHANGED=$(git diff --name-only "$BASE_REF" -- 'contracts/*.schema.json' || true)

if [ -z "$CHANGED" ]; then
  echo "✓ contracts-version-check: no schema changes"
  exit 0
fi

FAIL=0
for f in $CHANGED; do
  if [ ! -f "$f" ]; then
    # File deleted — major bump expected somewhere; not enforced here
    continue
  fi
  current_version=$(grep -E '"schemaVersion":' "$f" | head -1 | sed -E 's/.*"schemaVersion":[[:space:]]*"([^"]+)".*/\1/' || echo "missing")
  base_version=$(git show "$BASE_REF:$f" 2>/dev/null | grep -E '"schemaVersion":' | head -1 | sed -E 's/.*"schemaVersion":[[:space:]]*"([^"]+)".*/\1/' || echo "missing")

  if [ "$current_version" = "missing" ]; then
    echo "✗ $f: missing schemaVersion field"
    FAIL=1
    continue
  fi

  # If base didn't have the field, it's a new schema — pass
  if [ "$base_version" = "missing" ]; then
    echo "✓ $f: new schema at $current_version"
    continue
  fi

  if [ "$current_version" = "$base_version" ]; then
    # Body changed but version didn't bump
    echo "✗ $f: body changed but schemaVersion unchanged ($base_version)"
    echo "    See status/contracts/02_contracts_audit.md §2.6 for bump rules."
    FAIL=1
  else
    echo "✓ $f: $base_version → $current_version"
  fi
done

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "✗ Schema version check failed."
  exit 1
fi

echo "✓ All changed schemas have version bumps"
exit 0
