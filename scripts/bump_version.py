#!/usr/bin/env python3
"""Bump cloudmorph-tessera version across the 5 canonical sites.

Usage:
    python scripts/bump_version.py 0.4.0
    python scripts/bump_version.py 0.4.0 --dry-run
    python scripts/bump_version.py --validate
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9._-]+)?$")

# --------------------------------------------------------------------------- #
# Readers / writers per site                                                    #
# --------------------------------------------------------------------------- #


def _read_pyproject(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("pyproject.toml: version field not found")
    return m.group(1)


def _write_pyproject(root: Path, old: str, new: str, dry_run: bool) -> bool:
    path = root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    pattern = f'version = "{old}"'
    replacement = f'version = "{new}"'
    if pattern not in text:
        print(f"  WARN  pyproject.toml: expected '{pattern}' not found — skipping")
        return False
    new_text = text.replace(pattern, replacement, 1)
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def _read_version_module(root: Path) -> str:
    text = (root / "tessera" / "_version.py").read_text(encoding="utf-8")
    m = re.search(r'^    __version__ = "([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("tessera/_version.py: __version__ literal not found")
    return m.group(1)


def _write_version_module(root: Path, old: str, new: str, dry_run: bool) -> bool:
    path = root / "tessera" / "_version.py"
    text = path.read_text(encoding="utf-8")
    pattern = f'    __version__ = "{old}"'
    replacement = f'    __version__ = "{new}"'
    if pattern not in text:
        print(f"  WARN  tessera/_version.py: expected '{pattern}' not found — skipping")
        return False
    new_text = text.replace(pattern, replacement, 1)
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def _replace_version_in_doc(path: Path, old: str, new: str, dry_run: bool) -> int:
    """Replace all version occurrences in a doc file. Returns count of replacements."""
    text = path.read_text(encoding="utf-8")
    # Three patterns:  tessera:<old>   tessera <old>   Tessera v<old>
    patterns = [
        (f"tessera:{old}", f"tessera:{new}"),
        (f"tessera {old}", f"tessera {new}"),
        (f"Tessera v{old}", f"Tessera v{new}"),
    ]
    count = 0
    for pat, rep in patterns:
        occurrences = text.count(pat)
        if occurrences:
            text = text.replace(pat, rep)
            count += occurrences
    if count and not dry_run:
        path.write_text(text, encoding="utf-8")
    return count


def _write_changelog(root: Path, old: str, new: str, dry_run: bool) -> bool:
    """Insert a new release heading after the first `# Changelog` / preamble lines."""
    path = root / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    today = date.today().strftime("%Y-%m-%d")
    new_heading = f"\n## [{new}] — {today}\n"

    # Guard: don't add if already present
    if f"## [{new}]" in text:
        print(f"  SKIP  CHANGELOG.md: ## [{new}] already present")
        return False

    # Insert before the first existing ## [X.Y.Z] line
    insert_at = re.search(r"^## \[", text, re.MULTILINE)
    if insert_at:
        pos = insert_at.start()
        new_text = text[:pos] + new_heading + "\n" + text[pos:]
    else:
        # No existing version heading — append at end
        new_text = text.rstrip() + new_heading + "\n"

    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# Semver comparison (simple tuple comparison; handles pre-release as str)       #
# --------------------------------------------------------------------------- #


def _parse_semver(v: str) -> tuple[int, int, int, str]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(-.*)?$", v)
    if not m:
        raise ValueError(f"Not a valid semver string: {v!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), (m.group(4) or "")


def _semver_lt(a: str, b: str) -> bool:
    """Return True if a < b (numeric only; pre-release suffix not compared)."""
    ta = _parse_semver(a)[:3]
    tb = _parse_semver(b)[:3]
    return ta < tb


# --------------------------------------------------------------------------- #
# Validate mode                                                                 #
# --------------------------------------------------------------------------- #


def validate(root: Path) -> bool:
    """Check that all 5 sites agree on the current version. Returns True if OK."""
    try:
        v_pyproject = _read_pyproject(root)
    except Exception as exc:
        print(f"ERROR  pyproject.toml: {exc}")
        return False

    try:
        v_version_module = _read_version_module(root)
    except Exception as exc:
        print(f"ERROR  tessera/_version.py: {exc}")
        return False

    ok = True
    sites = {
        "pyproject.toml": v_pyproject,
        "tessera/_version.py": v_version_module,
    }
    for name, ver in sites.items():
        if ver != v_pyproject:
            print(f"MISMATCH  {name}: {ver!r} != pyproject {v_pyproject!r}")
            ok = False
        else:
            print(f"OK  {name}: {ver}")

    # Check doc files for presence of the version string
    for doc_rel in ("README.md", "docs/INSTALL.md"):
        doc_path = root / doc_rel
        if not doc_path.exists():
            print(f"SKIP  {doc_rel}: file not found")
            continue
        text = doc_path.read_text(encoding="utf-8")
        found = (
            f"tessera:{v_pyproject}" in text
            or f"tessera {v_pyproject}" in text
            or f"Tessera v{v_pyproject}" in text
        )
        if found:
            print(f"OK  {doc_rel}: contains version {v_pyproject}")
        else:
            print(f"WARN  {doc_rel}: version {v_pyproject} not found (doc may be stale)")

    changelog_path = root / "CHANGELOG.md"
    if changelog_path.exists():
        text = changelog_path.read_text(encoding="utf-8")
        if f"[{v_pyproject}]" in text:
            print(f"OK  CHANGELOG.md: contains [{v_pyproject}]")
        else:
            print(f"WARN  CHANGELOG.md: [{v_pyproject}] not found")

    return ok


# --------------------------------------------------------------------------- #
# Main bump logic                                                               #
# --------------------------------------------------------------------------- #


def bump(root: Path, new_ver: str, dry_run: bool = False) -> int:
    """Bump to new_ver. Returns 0 on success, non-zero on failure."""
    # Validate target format
    if not _SEMVER_RE.match(new_ver):
        print(f"ERROR  {new_ver!r} is not a valid semver string (expected X.Y.Z or X.Y.Z-pre)")
        return 1

    # Read current version
    try:
        current = _read_pyproject(root)
    except Exception as exc:
        print(f"ERROR  could not read current version: {exc}")
        return 1

    # Guard: same version
    if current == new_ver:
        print(f"ERROR  already at version {current}; nothing to do")
        return 1

    # Guard: downgrade
    if _semver_lt(new_ver, current):
        print(f"ERROR  target {new_ver} is older than current {current} — refusing downgrade")
        return 1

    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Bumping {current} → {new_ver}")
    print()

    changed: list[str] = []
    skipped: list[str] = []

    # 1. pyproject.toml
    if _write_pyproject(root, current, new_ver, dry_run):
        print(f"  {'(would update)' if dry_run else 'updated'}  pyproject.toml")
        changed.append("pyproject.toml")
    else:
        skipped.append("pyproject.toml")

    # 2. tessera/_version.py
    if _write_version_module(root, current, new_ver, dry_run):
        print(f"  {'(would update)' if dry_run else 'updated'}  tessera/_version.py")
        changed.append("tessera/_version.py")
    else:
        skipped.append("tessera/_version.py")

    # 3. README.md
    readme_path = root / "README.md"
    if readme_path.exists():
        count = _replace_version_in_doc(readme_path, current, new_ver, dry_run)
        if count:
            print(f"  {'(would update)' if dry_run else 'updated'}  README.md ({count} occurrence(s))")
            changed.append("README.md")
        else:
            print(f"  SKIP  README.md: no version occurrences found for {current}")
            skipped.append("README.md")
    else:
        print("  SKIP  README.md: file not found")
        skipped.append("README.md")

    # 4. docs/INSTALL.md
    install_path = root / "docs" / "INSTALL.md"
    if install_path.exists():
        count = _replace_version_in_doc(install_path, current, new_ver, dry_run)
        if count:
            print(f"  {'(would update)' if dry_run else 'updated'}  docs/INSTALL.md ({count} occurrence(s))")
            changed.append("docs/INSTALL.md")
        else:
            print(f"  SKIP  docs/INSTALL.md: no version occurrences found for {current}")
            skipped.append("docs/INSTALL.md")
    else:
        print("  SKIP  docs/INSTALL.md: file not found")
        skipped.append("docs/INSTALL.md")

    # 5. CHANGELOG.md
    if _write_changelog(root, current, new_ver, dry_run):
        print(f"  {'(would add)' if dry_run else 'added'}  CHANGELOG.md: ## [{new_ver}] — {date.today().strftime('%Y-%m-%d')}")
        changed.append("CHANGELOG.md")
    else:
        skipped.append("CHANGELOG.md")

    print()
    print(f"Summary: {len(changed)} site(s) {'would be ' if dry_run else ''}updated — {', '.join(changed) or 'none'}")
    if skipped:
        print(f"         {len(skipped)} site(s) skipped — {', '.join(skipped)}")
    if dry_run:
        print("(dry run — no files written)")
    return 0


# --------------------------------------------------------------------------- #
# Entry point                                                                   #
# --------------------------------------------------------------------------- #


def main() -> None:
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    dry_run = "--dry-run" in args
    validate_mode = "--validate" in args
    positional = [a for a in args if not a.startswith("--")]

    if validate_mode:
        ok = validate(ROOT)
        sys.exit(0 if ok else 1)

    if not positional:
        print("ERROR  missing target version argument")
        print("Usage: python scripts/bump_version.py <X.Y.Z> [--dry-run]")
        sys.exit(1)

    target = positional[0]
    sys.exit(bump(ROOT, target, dry_run=dry_run))


if __name__ == "__main__":
    main()
