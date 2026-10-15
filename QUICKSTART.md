# Local source quickstart

This guide covers the staged Tessera source checkout and its offline synthetic
pilot. It does not depend on Tessera Cloud or establish a published package,
desktop-client compatibility, production deployment, or customer acceptance.

## 1. Prepare the source checkout

The project metadata declares Python `>=3.12`. Retained local evidence includes
an editable Tessera source install on CPython 3.13.7 on Windows and existing
tests on CPython 3.12.3 in WSL. The separate pinned Hermes client environment
needed a dependency-marker workaround; Tessera did not. These runs do not
establish a general OS/Python support matrix. The source declares version
`1.0.0`; PyPI and container publication of that version are unverified. See [installation notes](docs/INSTALL.md).

From a checkout with its runtime dependencies available:

```bash
python -m pip install -e .
tessera init --dir ./tessera-local
```

`tessera init` creates a `log_only` configuration and two reference policies.
Configure the upstream and review the policies before starting a live proxy.

## 2. Run the offline synthetic pilot

This command passed all 15 checks on WSL Ubuntu with CPython 3.12.3. Run it in
Linux/WSL, following the [synthetic pilot instructions](examples/synthetic_pilot/README.md),
and use a new evidence directory:

```bash
python examples/synthetic_pilot/run.py --output-dir ./tessera-local-evidence
```

On Windows, this pilot currently fails before requests because its deliberate
IP-socket guard blocks asyncio's internal socket pair. The guard remains enabled;
use the verified Linux/WSL path for this script. This limitation is separate from
the successful Windows Tessera install and Hermes connector test.

The pilot exercises the local policy engine and HTTP adapter with synthetic
tools. It starts no listener, calls no cloud or model API, and does not validate
a live IDE/client connection. Its unsigned records are local test evidence, not
an independent assessment or production acceptance.

## 3. Configure a client separately

For a real MCP client, configure a reviewed upstream, local auth, policies, and
the URL for the selected route. See [the staged Streamable HTTP contract](docs/STREAMABLE_HTTP.md)
and [integration recipes](docs/INTEGRATIONS.md). Compatibility is specific to
the named client and protocol version tested; the local synthetic pilot does
not establish compatibility for every desktop client.
