/**
 * Hash chain bookkeeping for the audit log.
 * Mirrors the Python impl in cloudmorph_common.audit.chain.HashChain.
 *
 * Stamps prevEventHash + eventHash on events and advances the per-tenant head.
 * Verifier walks the chain and asserts no gap.
 */

import { createHash } from "node:crypto";

import { canonicalJson } from "./canonical-json.js";

export interface AuditEventLike {
  tenantId: string;
  eventHash?: string;
  prevEventHash?: string;
  signature?: string;
  [key: string]: unknown;
}

export class HashChain {
  private heads = new Map<string, string>();

  head(tenantId: string): string {
    return this.heads.get(tenantId) ?? "";
  }

  restoreHead(tenantId: string, headHash: string): void {
    if (headHash && (headHash.length !== 64 || !/^[a-f0-9]+$/.test(headHash))) {
      throw new Error(`restoreHead: invalid sha256 hex digest: ${headHash}`);
    }
    this.heads.set(tenantId, headHash);
  }

  stamp<E extends AuditEventLike>(event: E): E & { prevEventHash: string; eventHash: string } {
    if (!event.tenantId || typeof event.tenantId !== "string") {
      throw new Error("HashChain.stamp: event must have a non-empty string tenantId");
    }
    const prev = this.heads.get(event.tenantId) ?? "";
    const stamped: E & { prevEventHash: string; eventHash: string } = {
      ...event,
      prevEventHash: prev,
      eventHash: "",
    };
    const digestInput = { ...stamped, eventHash: "", signature: "" };
    const eventHash = createHash("sha256").update(canonicalJson(digestInput)).digest("hex");
    stamped.eventHash = eventHash;
    this.heads.set(event.tenantId, eventHash);
    if (!stamped.signature) {
      delete stamped.signature;
    }
    return stamped;
  }

  static verifyPair(prev: AuditEventLike, next: AuditEventLike): boolean {
    return (next.prevEventHash ?? "") === (prev.eventHash ?? "");
  }

  static verifyEventHash(event: AuditEventLike): boolean {
    const stored = event.eventHash ?? "";
    if (!stored) return false;
    const digestInput = { ...event, eventHash: "", signature: "" };
    const recomputed = createHash("sha256").update(canonicalJson(digestInput)).digest("hex");
    return stored === recomputed;
  }
}
