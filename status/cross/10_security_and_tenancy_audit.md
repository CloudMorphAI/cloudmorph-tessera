# 10 — Security & Tenancy Audit

_The enterprise-sales blocker section. Every gap here is a "no" from a compliance buyer. Most are unbuilt today._

---

## 1.1 Secrets in repo

Audit of what could be in the worktree:

- [.gitignore](../../.gitignore) covers `.env`, `*.pem`, `*.key`, `credentials.json`, `service-account.json`, `*_secret*`, `*_credential*`. **Solid baseline.**
- [.env.example](../../cloudmorph-mcp/.env.example) has placeholder values only — no real secrets.
- `rzp-key.csv` exists in the parent `CloudMorph` working directory (Razorpay key — **outside this repo**, but neighboring file is concerning). **Action:** verify it's gitignored at parent repo level too.
- Grep for high-risk strings in this repo:
  ```
  grep -r 'AKIA[0-9A-Z]{16}' .   # AWS access key prefix
  grep -r 'sk-ant-'              # Anthropic key prefix  
  grep -r 'sk_live_'             # Stripe live key prefix
  grep -r 'BEGIN.*PRIVATE KEY'   # PEM blocks
  ```
  All should return nothing. (Not run this pass — add to Block A pre-commit.)

**Findings:**
- **P0:** Add `gitleaks` (or `truffleHog`) pre-commit hook + CI gate. Effort 2h. Block A.
- **P1:** Document a SECURITY.md disclosure policy (where to report vulns; coordinate with the upstream Console).

---

## 1.2 Tenant isolation

Today: **the MCP server doesn't know what tenant a token belongs to.** Every request relies on upstream to scope the response. Multi-tenant defects today don't have a chance to surface because there's no local state to leak across tenants.

After Block C/D/E (when local state shows up):

| Surface | Isolation requirement |
|---|---|
| Policy bundle | Per-tenant, namespaced load (`POLICY_BUNDLE_PATH=/etc/cloudmorph/bundles/${tenantId}/active.tar.gz` or URL with `${tenantId}` template) |
| Decision cache | Cache key includes `tenantId` — see [policy/05 §1.10](../policy/05_policy_engine_design.md). **Verify with adversarial test: cache poisoning via tenant-id collision.** |
| Audit log sink | Per-tenant prefix on S3 (`s3://audit-bucket/${tenantId}/...`); per-tenant retention policy |
| Session store | Session id namespace per tenant (`tnt_abc:ses_xyz`); collision impossible |
| Intent store | Same as session store |
| Approval queue | Per-tenant — approvers see only their tenant's approvals |
| Rate-limit | Per-token (which is already per-tenant via `tokenResolver`) — verify no cross-tenant burst-borrow |
| Metrics | Labels include `tenantId` — careful with cardinality if many tenants |

**Cross-tenant leak adversarial fixtures (Block H):**
- `adv_tenant_cache_collision` — request a decision; rotate to different tenant with same shape; verify second request not served from cache.
- `adv_tenant_audit_namespace` — emit event for tenant A; subscribe to tenant B's audit stream; verify zero events.
- `adv_tenant_session_id_collision` — create session "ses_X" for tenant A and tenant B; verify session lookup filters by tenant.

---

## 1.3 mTLS option for self-hosted

For air-gapped or zero-trust deployments. Customer terminates TLS with their own CA; client cert maps to tenant identity.

```bash
MCP_MTLS_ENABLED=true
MCP_MTLS_CA_CERT=/etc/cloudmorph/ca.crt
MCP_MTLS_TENANT_FROM_CN=true   # extract tenant from cert CN
```

Server-side:
```typescript
const server = https.createServer({
  cert, key,
  ca: fs.readFileSync(MCP_MTLS_CA_CERT),
  requestCert: true,
  rejectUnauthorized: true,
}, app);

app.use((req, res, next) => {
  const peerCert = (req.socket as any).getPeerCertificate();
  if (!peerCert || !peerCert.subject) return res.status(401).end();
  req.tenantId = peerCert.subject.CN;
  next();
});
```

**Effort:** 6h. Block I if a self-hosted design partner needs it; otherwise post-MVP.

---

## 1.4 OIDC for human approvers

The token model is for agents. Human approvers (the people who decide on `cloudmorph_approve`) should authenticate via OIDC.

```
GET /approve/login?provider=okta&approval_id=app_xyz
  → 302 → Okta authorize URL
GET /approve/oidc/callback?code=...
  → mints short-lived approver session token
  → 302 → /approve/ui?approval_id=app_xyz&session=...
```

Provider configurations:
- Google Workspace
- GitHub OAuth
- Okta / OIDC-generic
- Azure Entra ID

Approver identity → audit log `actorId`. Multi-party approval (M-of-N) requires verifying M distinct OIDC identities.

**Effort:** 12h MVP (one provider — Google), 8h per additional. Block I (stretch) or post-MVP.

---

## 1.5 API key rotation

Today: tokens minted upstream, rotation flow not visible in this repo. For the MCP server's own admin key (`MCP_EVENT_SECRET`):

- Add rotation endpoint: `POST /admin/rotate-event-secret` (requires admin auth)
- Support two valid secrets simultaneously during rotation window (24h)
- Audit emit on rotation

**Effort:** 4h. Block H or I.

For tenant tokens: handled upstream by Console; out of MCP scope.

---

## 1.6 Audit log tamper-evidence

Already designed in [contracts/02 §2.3](../contracts/02_contracts_audit.md) and [mcp/01 §1.7](../mcp/01_server_audit.md). Recap:

- **Hash chain:** `eventHash = sha256(canonicalJson({...event, eventHash: "", signature: ""}))` and each event has `prevEventHash` pointing back. Tampering breaks the chain.
- **Canonical-JSON: RFC 8785 (JCS).** Stable serialization across implementations.
- **Optional per-event Ed25519 signature** for highest-assurance tenants.
- **Verifier CLI:** `npx @cloudmorph/audit-verify --bundle s3://customer-bucket/audit/...` walks chain, asserts no gap.

The verifier CLI is the customer-facing trust artifact — they can run it independently and prove their audit log is intact.

**Severity:** P0 (chain) + P1 (per-event sig + verifier CLI). Effort: 18h MVP (chain + sinks + CLI). Block D.

---

## 1.7 Data residency

Per-region buckets for audit logs. **Customer-owned sinks are the high-end story** — audit data lives in customer's S3 bucket in customer's account, MCP server has cross-account-role write-only access.

Configuration:
```bash
AUDIT_SINK=s3-customer-owned
AUDIT_S3_BUCKET=customer-acme-cloudmorph-audit
AUDIT_S3_REGION=us-east-1
AUDIT_S3_ROLE_ARN=arn:aws:iam::123456789012:role/CloudmorphAuditWriter
AUDIT_S3_OBJECT_LOCK=true   # WORM
AUDIT_S3_RETENTION_DAYS=2555 # 7 years
```

For EU customers: same shape with `eu-west-1`. The MCP-server-tenant-routing must NOT cross regions; tenant-region binding is part of `TokenResolver` output.

**Effort:** 8h customer-owned sink. Post-MVP unless first design partner asks for it.

---

## 1.8 Compliance posture

Aspirations vs reality:

| Standard | Aspires to | Today |
|---|---|---|
| SOC 2 Type 2 | yes (within 12 months of revenue) | not started; audit prep would take ~6 months |
| ISO 27001 | yes (post-SOC 2) | not started |
| HIPAA | maybe; only if a healthcare design partner | BAA model not designed |
| GDPR | yes (EU customers) | data-residency story missing; data-deletion SLA missing |
| PCI DSS | no (we don't touch card data) | N/A |
| FedRAMP | no (US public sector) | N/A; revisit at $5M+ ARR |

**MVP-relevant:**
- **Data retention:** per-tenant configurable (default 30 days for hosted, 7 days for free tier).
- **Data deletion SLA:** `DELETE /tenant/${tenantId}/data` flushes audit, sessions, decisions; complete in 30 days. Block I.
- **Encryption at rest:** S3 default SSE; RDS/ElastiCache encryption-at-rest enabled by Terraform default.
- **Encryption in transit:** TLS 1.3 only on MCP HTTPS endpoint; cert via ACM (hosted) or customer-provided (self-hosted).
- **Breach notification:** SECURITY.md with email + 72h SLA per GDPR. Block A.

---

## 1.9 Fail-open vs fail-closed

Per-tenant config (default fail-closed). When fail-open active:
- Every request returns `audit_only` decision
- Audit emits prominent event (`policy.engine.fail_open_active`)
- Metrics counter increments (`cm_policy_fail_open_seconds_total`)
- Customer dashboard prominently shows fail-open status

Failure modes triggering fail-mode evaluation:
- Policy engine crash → respect tenant fail-mode
- Bundle load failure → continue with previous bundle (not fail-mode); only fail-mode if no bundle available
- Audit sink failure → buffer to local disk (bounded queue); emit alert; never block evaluation
- Executor unreachable → executor-side concern (continue claimed job, fail-closed on next claim)

**Effort:** 3h fail-mode config (within Block H). Failure mode catalog already in [policy/05 §1.14](../policy/05_policy_engine_design.md).

---

## 1.10 Failure mode catalog

Single source of truth (canonical):

| Failure | Impact | Detection | Mitigation | Severity |
|---|---|---|---|---|
| Policy engine OOM | All decisions error | OTel span err counter | k8s OOM-kill restart; per-tenant fail-mode | P0 |
| Policy bundle signature invalid | Reload aborts | bundle.reload audit + alert | Continue prev bundle | P0 |
| Audit sink (S3) unreachable | Events stack up | sink-emit failure metric | Buffer to disk (bounded 100 MB); drop-oldest with alert; backpressure on emitter | P0 |
| Audit local disk full | Buffer drops | disk-usage alert | Same; alert + page if > 80% | P1 |
| Redis unreachable (rate-limit, session) | Rate-limit fails open OR session lookups fail | Connect failure metric | Per-tenant fail-mode for sessions; rate-limit fails open with audit warning | P1 |
| Executor disconnected | Claimed jobs orphaned | Heartbeat timeout server-side | Lease expiry + re-claim by another executor | P1 |
| Token resolver upstream unreachable | Token validation fails | Resolver miss rate | Per-tenant fail-mode; for hosted, fail-closed (security default) | P0 |
| TLS cert expiry | TLS handshakes fail | cert-expiry monitor (30d before) | ACM auto-renew (hosted) | P0 |
| Bundle URL DDoS | Bundle reloads fail | reload metrics | Use signed URL with caching CDN | P2 |

---

## 1.11 Threat model — abridged

(Full threat model is a separate exercise; this is the abridged for MVP authoring.)

**In-scope threats:**
1. Mis-aligned agents (do something they shouldn't, even without an attacker) — **product purpose**.
2. Adversarial prompts to agents trying to bypass intent (`adv_intent_spoofing` fixtures).
3. Compromised tokens (rotated quickly, audit shows what they did).
4. Cross-tenant data leakage (decision cache, audit, session).
5. Tampering with audit log (chain detects, signature optional).
6. Replay attacks (event timestamps + nonce).
7. Compromised executor (limited blast — confined to one cloud account; audit shows everything).

**Out-of-scope:**
1. Compromise of the tenant's cloud credentials (orthogonal — that's IAM's problem, although our compile-to-IAM mitigates).
2. Network-level MITM (TLS handles).
3. Side-channel attacks on the policy engine (would require WASM sandbox escape — we don't defend).
4. Insider threat at CloudMorph itself (separate org-level controls).

---

## 1.12 Severity table

| Item | Severity | Effort | Block |
|---|---|---:|---|
| Add `gitleaks` pre-commit + CI gate | P0 | 2h | A |
| SECURITY.md with disclosure policy | P0 | 1h | A |
| Tenant-id in decision cache key | P0 | (in policy/05) | E |
| Tenant-id in audit log namespace | P0 | (in mcp/01 §1.7) | D |
| Cross-tenant adversarial fixtures (3) | P0 | 4h | H |
| Audit hash chain | P0 | (in mcp/01 §1.7) | D |
| Audit verifier CLI | P1 | 6h | D |
| Per-event Ed25519 signature | P1 | 6h | post-MVP |
| Per-tenant audit retention config | P1 | 3h | I |
| Customer-owned audit sink | P1 | 8h | post-MVP |
| Per-tenant fail-mode config | P1 | 3h | H |
| Failure mode catalog tested | P1 | 6h | H |
| mTLS option | P2 | 6h | post-MVP |
| OIDC for approvers (Google) | P1 | 12h | I (stretch) |
| OIDC additional providers | P2 | 8h each | post-MVP |
| API key rotation flow | P2 | 4h | post-MVP |
| Data deletion SLA endpoint | P1 | 6h | I |
| Encryption at rest (Terraform default) | P0 | 1h | I |
| TLS 1.3 only on hosted | P0 | 1h | I |

**MVP critical-path total: ~30h.** Block A + D + H + I.

---

## 1.13 Compliance certifications roadmap

Not for MVP, but for the next 12 months:

```
Day 0   - 90:    Threat model documented; SECURITY.md; basic controls in place.
Day 90  - 180:   Hire/contract a SOC 2 auditor; implement controls; write evidence.
Day 180 - 365:   Type 1 audit; remediate findings; prepare for Type 2.
Day 365 +:       Type 2 (continuous monitoring); ISO 27001 readiness.
```

What MVP needs to NOT preclude later certification:
- Audit log retention (✓ — we have it)
- RBAC and least-privilege at infra (✓ — Terraform-enforced)
- Encryption everywhere (✓)
- No customer data in our staff's hands (✓ — customer-owned audit sinks design)

---

## 1.14 Source links

- [.gitignore](../../.gitignore)
- [.env.example](../../cloudmorph-mcp/.env.example)
- [policy/05_policy_engine_design.md](../policy/05_policy_engine_design.md) — fail modes
- [mcp/01_server_audit.md §1.7](../mcp/01_server_audit.md) — audit chain
- [contracts/02_contracts_audit.md §2.3 AuditEvent](../contracts/02_contracts_audit.md)
