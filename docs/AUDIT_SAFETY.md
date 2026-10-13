# Audit privacy, integrity and signer trust

Configure minimal audit payloads explicitly when argument retention is unwanted:

```yaml
audit:
  path: /var/lib/tessera/audit.db
  payload_mode: minimal
  also_stdout: false
```

`minimal` retains decision/mode/policy, configured upstream/tool names, principal,
generated request linkage and selected cost metadata. It removes tool arguments,
request metadata, conversation IDs, free-form reasons/errors and other context
before queueing, hashing and audit sinks, including the optional stdout sink.
Policy evaluation and the forwarded request still receive their original input.
The recorded `audit_payload_mode` identifies minimized records.

`full` remains the default for compatibility and retains raw context. This setting
does not rewrite historical audit records, control all application logs or detect
secrets embedded in retained identifiers/names. Principal metadata may be personal
data. Agree access, retention and deletion requirements before customer-data use;
do not redact existing chained records while claiming their original integrity.

On POSIX systems SQLite audit files are opened/created owner-only0600 before use;
existing DB/WAL/SHM files are restricted too. Symlink audit paths are rejected where
O_NOFOLLOW is available. Some mounted filesystems ignore chmod; the runtime warns
instead of claiming enforcement. Windows/NTFS ACLs and parent-directory access
must be configured by the operator. A verified hash chain does not encrypt data.

## Verify a signer against an independent public key

```text
tessera audit verify --dir ./evidence --trusted-key ./approved-signer-public.pem
```

Provision that public key through an independently trusted channel; copying the
key from the bundle into this file does not establish identity. The explicit key
must match the embedded Ed25519 key and the valid signature. A wrong/malformed key,
missing signed manifest or invalid signature fails verification. The option is
only valid with `--dir`; it does not authenticate bare JSONL or a live database.

Without `--trusted-key`, existing chain/self-signature integrity checking remains
available, but output labels signer trust as untrusted. JSON `signature_ok`
describes signature consistency; `signature_trusted` is true only after successful
verification against the caller's external anchor. Neither proves the signer is
CloudMorph, a customer or an approved organization without key-owner provisioning.

## Audit failure and completeness

Audit remains best effort. Queue overflow, shutdown or a sink failure can leave a
returned event ID without a durable row. Sink failures increment
`tessera_audit_emit_failures_total`; dropped events are exposed through
`tessera_audit_emit_dropped_total`. Sink failure logs omit exception text to avoid
echoing sensitive content. Monitor both counters and the sink itself.

Synchronous audit mode does not make swallowed sink failures fail closed. These
changes do not add transactional execution/audit guarantees, retries of mutations,
lossless storage or an automatic retention service. A valid chain can still omit
the final lost event; proxy success also does not independently prove an external
side effect. Stronger delivery/access/identity requirements remain pilot gates.

The current Work connection is an anonymous, fixed-fictional fixture. These audit
controls do not turn it into an authenticated customer endpoint or public service.
