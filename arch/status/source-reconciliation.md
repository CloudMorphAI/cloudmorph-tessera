# Current source boundaries — 13 October 2026

CM-036 reconciled the local release source after CM-033. Current executable
code has one `PolicyEngine` (`tessera/policy/engine.py`), one runtime `Policy`
schema (`tessera/policy/schema.py`) and one `HashChain`
(`tessera/audit/chain.py`). The HTTP proxy, CLI evaluator, audit emitter and
verifier import those shared implementations. No competing production
engine/schema/hash-chain implementation was found under `tessera/`.

The file named `examples/panel_demo/engine.py` is an isolated demonstration
runner and evidence projection. It calls `tessera.demo`, which uses the real
proxy; it is not an alternative policy evaluator. The prompt demonstration
also uses the existing proxy and synthetic executors. Historical phase plans
do not prove that a parallel implementation exists in this checkout.

## Retained source and release intent

- Keep the accepted Streamable HTTP, bounded discovery and catalog trust work
  in their existing modules. Do not introduce another gate or policy language.
- Keep `tessera/demo.py`, `tests/test_demo.py`, panel/prompt examples and their
  YAML dependencies unchanged through the 14 October presentation. The existing
  GitHub/Postgres bundled policies are required by that demonstration; deleting
  them would break its imports/resource lookup. They remain in the working tree.
- The public wheel currently packages `tessera/demo.py` and the bundled YAMLs,
  including untracked YAMLs. The local source state is not a release boundary:
  the next public release must explicitly review and include intended files.
  Do not stage the whole dirty tree or publish CM-033's private review wheel.
- Keep the public runtime and privately authored intelligence separate. The
  hosted wrapper remains an optional adapter, with its own CM-053 follow-up.
- CM-055 owns starter scaffolding using the existing loader and engine. It must
  select a small explicit starter set rather than copying all illustrative
  policies. CM-049 owns selected vendor/native-tool content in the intelligence
  source. During the demo freeze it must not rewrite the bundled demo policies.

The source review is complete; publication, real-client certification and the
remaining content repairs are separate work. Evidence is in workspace maintenance
`private/cm036/source-review.json`; the expanded freeze manifest includes the
demonstration's bundled policy dependencies. No unique source was removed.
