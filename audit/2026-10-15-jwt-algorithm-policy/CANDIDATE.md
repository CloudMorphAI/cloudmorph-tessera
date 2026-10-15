# JWT algorithm allow-list candidate

## Current-base integration - 15 October 2026

Arjun/Bheem integrated the two original commits onto current main
`aeb82fea191781f5fa9e1de33311137d036e4d99`, preserving the base-image refresh.
Local cherry-picks: `0df191e` and `7cfad00`. The original candidate worktree and
receipts remain unchanged. Current-source import was explicitly verified before
rerunning the same focused suite in the existing isolated WSL environment:
Python 3.12.3, python-jose 3.5.0; **26 passed in 2.72 seconds**. Ruff and Git
whitespace checks pass. The first native environment attempt lacked python-jose
and stopped at collection; it is not counted as a product failure or test pass.

Drona independently found no concrete introduced blocker and cleared focused
DRAFT publication. No auth merge, deployment, production configuration or live
issuer compatibility is accepted. The separate conditional introspection/PyJWT
finding is unchanged; the inspected AWS gateway did not expose that route.
No cloud/provider request, real credential, signing key, or token was used.
Individual RS384/ES384/ES512 cases and live providers remain untested here.

## Preserved original candidate receipt

Status: local review candidate only. The isolated branch was created from
`origin/main` at `abc838992cac4139e5ee6d539efe920b1be62254`. The requester later
confirmed remote main at `aeb82fea191781f5fa9e1de33311137d036e4d99`; this candidate
remains based on `abc8389` and was not rebased. Integration is left to Arjun. It
is not pushed, merged, released, signed, deployed, or evaluated against production
configuration. Final security and founder review is required before rollout.

## Threat hypothesis

`tessera/auth/_jwks.py::validate_jwt` parses the unverified JWT header, selects a
JWKS key by `kid` from the configured endpoint, then passes `alg` from that
unverified header as the entire `python-jose` `algorithms` allow-list. A caller
who can submit a token can therefore ask the verifier to use an algorithm chosen
by that token. With a trusted RSA JWK, a header-confusion attempt could try an
HMAC algorithm or another asymmetric algorithm. Whether `python-jose` 3.5.0
accepts a forged HMAC token with the actual JWK object used on this path is not
proven here; this is a threat hypothesis, not a reproduced authentication bypass.

The configured issuer, audience, JWKS URL, and `kid` lookup remain the trust
inputs. The code does not select a tenant from the JWT algorithm or change
principal/scope derivation.

## Behavior change

- Before: the token header supplied a singleton accepted-algorithm list; missing
  `alg` fell back to `RS256`. The selected key still came from the configured
  JWKS URL using `kid`.
- Candidate: the validator allows only the fixed asymmetric JWS set `RS256`,
  `RS384`, `RS512`, `ES256`, `ES384`, and `ES512`. It rejects other header
  algorithms before fetching a key, requires the selected JWK key type to match,
  and requires any JWK `alg` metadata to match the token header. `python-jose`
  receives the same fixed allow-list. Signature, issuer, audience, expiration,
  and configured clock-leeway checks are unchanged. Both `JWTAuthenticator` and
  `OIDCAuthenticator` use this shared validator.
- Tests use generated offline RSA/ECDSA keys, public synthetic JWK data, and
  mocked JWKS HTTP. They do not contact an identity provider or use real
  credentials.

## Provider and compatibility assessment

Repository configuration documents MCP JWT auth with Entra, Okta, and Cognito;
management-plane OIDC documents Clerk, Auth0, Cognito, and a generic custom
provider. Existing provider samples and unit fixtures use RSA/RS256. Neither
configuration model exposes a trusted algorithm allow-list, and no production
issuer configuration was inspected or changed.

The installed `python-jose` 3.5.0 JWS registry supports RS256/384/512,
ES256/384/512, and HS256/384/512; it does not register PS or EdDSA. The candidate
preserves its RSA and ECDSA public-key signature families and rejects symmetric
HMAC and `none`. This is a fixed allow-list, not a new production setting. The
repository documents generic OIDC providers, so issuers using RSA or ECDSA JWS
with matching JWKS metadata remain compatible. An issuer using another algorithm
family or symmetric JWKS key is rejected; supporting it requires a separately
reviewed trusted configuration change. No real token or production configuration
value was read.

## Scope and residuals

This change is limited to the shared `python-jose` JWKS validator. Read-only
inspection confirms the separate `_verify_token_signature` helper in
`tessera/auth/oauth_rs.py` takes `alg` from the unverified header and passes it to
PyJWT as `algorithms=[alg]`. The installed PyJWT 2.13.0 registry includes ES and
PS algorithms. That helper was not modified and needs separate scoped review.
This candidate does not establish exploitability, compatibility against live
issuers, production configuration, deployment behavior, or independent security
certification.

## Verification

The approved WSL environment reports `python-jose` 3.5.0 and PyJWT 2.13.0.
`/home/sainath/tessera-test-venv/bin/python -m pytest -q
tests/unit/auth/test_jwt_mcp.py tests/unit/auth/test_oidc.py` passed: **26 passed
in 1.97s**. This includes valid RS256/RS512/ES256 tokens, a valid JWK without
optional `alg`, existing issuer/audience/expiry and OIDC auth cases, plus
rejection of `none`, HMAC, tampered signatures, JWK algorithm/key-family
mismatches, and missing/malformed header `alg` before JWKS fetch. Ruff and
`git diff --check` passed. No cloud call, external endpoint, image scan, release
operation, or deployment was attempted.
