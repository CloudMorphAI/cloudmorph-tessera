# 01 — Fresh install + import smoke

## Starting state
- Clean Python 3.11+ virtualenv (`python -m venv .venv && source .venv/bin/activate`)
- No `cloudmorph-tessera` previously installed
- Internet access to PyPI

## User actions (step-by-step)
1. `pip install cloudmorph-tessera`
2. `python -c "import tessera; print(tessera.__version__)"`
3. `tessera --version` (CLI entrypoint)
4. `tessera --help` (subcommand listing)

## Expected observable result
- Pip resolves and installs without dependency conflicts; final line includes `cloudmorph-tessera-0.5.0` (or current released version)
- `tessera.__version__` prints the same version string
- `tessera --version` prints the same version string and exits 0
- `tessera --help` lists at least: `serve`, `config`, `policy`, `intelligence`, `audit`
- `tessera/intelligence/public_key.pem` is bundled inside the installed package (verify with `python -c "import importlib.resources, tessera.intelligence; print(importlib.resources.files(tessera.intelligence).joinpath('public_key.pem').read_bytes()[:30])"`)

## Failure modes to watch for
- Dependency resolver error citing `PyJWT` or `cryptography` → owner: `pyproject.toml` `[project.dependencies]`
- `ImportError: cannot import name X from tessera` → owner: package `__init__.py` re-exports
- Missing `public_key.pem` → owner: `MANIFEST.in` / `pyproject.toml` `package-data`
- CLI subcommand missing → owner: `tessera/cli/` registration

## How to verify manually

```bash
python -m venv /tmp/tessera-smoke && source /tmp/tessera-smoke/bin/activate
pip install cloudmorph-tessera
python -c "import tessera; print(tessera.__version__)"
tessera --version
tessera --help
```

## Owner on failure
Packaging layer — `pyproject.toml`, `MANIFEST.in`, `tessera/__init__.py`, `tessera/cli/`

## Related code
- `pyproject.toml`
- `tessera/__init__.py`
- `tessera/cli/__init__.py`
- `tessera/intelligence/public_key.pem`
