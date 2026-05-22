# code-audit: Hardcoded Cognito pool ID + client ID in cloud-wrapper auth (HIGH)

**Discovered**: 2026-05-22 code-audit overnight pass
**Severity**: HIGH

## Problem
`tessera-cloud-wrapper/tessera_cloud/auth/cognito.py:62-66` `CognitoJWTAuthenticator.__init__` has hardcoded fallback defaults `"us-east-1_QNrpiKCcX"` (Cognito user pool ID) and `"6bvrifvhga0rotlp1apo9jel89"` (app client ID). These are real production identifiers committed to source. They're overridable by env vars but the defaults will be used in any misconfigured container.

Cognito Pool/Client IDs aren't secrets per se, but their exposure in source enables targeted Cognito enumeration (`InitiateAuth` user-enumeration) and phishing flows that look authentic against a real client ID.

## Where
- File: `tessera-cloud-wrapper/tessera_cloud/auth/cognito.py:62-66`
- Class: `CognitoJWTAuthenticator.__init__`

## Suggested fix
Remove the hardcoded fallbacks entirely. Raise `ConfigError` at construction time if `TESSERA_CLOUD_COGNITO_POOL_ID` or `TESSERA_CLOUD_COGNITO_CLIENT_ID` are unset. Match the pattern used elsewhere in the project for required config (fail loudly at startup, never silently fall back to a hardcoded value).

The CDK construct that deploys the cloud-wrapper should set these env vars explicitly from CDK context.

## Effort
small (delete fallback + raise; CDK side may need 2 lines to ensure env vars are passed)

## Acceptance criteria
- Source no longer contains `"us-east-1_QNrpiKCcX"` or `"6bvrifvhga0rotlp1apo9jel89"`
- Container starts cleanly when env vars are present
- Container fails fast (clear error message) when env vars are absent
- CDK construct passes both env vars to the Fargate task

## On merge
Folds into the cloud-wrapper auth status doc. This file deleted after merge.
