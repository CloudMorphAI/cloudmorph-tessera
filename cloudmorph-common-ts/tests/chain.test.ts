import { describe, expect, it } from "vitest";
import { HashChain } from "../src/audit/chain.js";

const makeEvent = (tenantId: string, payload: Record<string, unknown> = { outcome: "allow" }) => ({
  schemaVersion: "v0.1",
  eventId: "evt_test_xyz",
  tenantId,
  eventType: "decision.made",
  payload,
  occurredAt: "2026-04-23T10:00:00Z",
});

describe("HashChain stamping", () => {
  it("first event has empty prevEventHash", () => {
    const chain = new HashChain();
    const stamped = chain.stamp(makeEvent("tnt_a"));
    expect(stamped.prevEventHash).toBe("");
    expect(stamped.eventHash).toMatch(/^[a-f0-9]{64}$/);
  });

  it("second event links to first", () => {
    const chain = new HashChain();
    const first = chain.stamp(makeEvent("tnt_a"));
    const second = chain.stamp(makeEvent("tnt_a", { outcome: "deny" }));
    expect(second.prevEventHash).toBe(first.eventHash);
  });

  it("chain isolated per tenant", () => {
    const chain = new HashChain();
    const a1 = chain.stamp(makeEvent("tnt_a"));
    const b1 = chain.stamp(makeEvent("tnt_b"));
    expect(a1.prevEventHash).toBe("");
    expect(b1.prevEventHash).toBe("");
    const a2 = chain.stamp(makeEvent("tnt_a"));
    expect(a2.prevEventHash).toBe(a1.eventHash);
  });

  it("rejects events without tenantId", () => {
    const chain = new HashChain();
    expect(() => chain.stamp({ eventType: "x" } as never)).toThrow(/tenantId/);
  });
});

describe("HashChain verification", () => {
  it("verifyPair detects mismatch", () => {
    const chain = new HashChain();
    const first = chain.stamp(makeEvent("tnt_a"));
    const second = chain.stamp(makeEvent("tnt_a"));
    expect(HashChain.verifyPair(first, second)).toBe(true);
    const tampered = { ...second, prevEventHash: "0".repeat(64) };
    expect(HashChain.verifyPair(first, tampered)).toBe(false);
  });

  it("verifyEventHash detects payload tampering", () => {
    const chain = new HashChain();
    const stamped = chain.stamp(makeEvent("tnt_a"));
    expect(HashChain.verifyEventHash(stamped)).toBe(true);
    const tampered = { ...stamped, payload: { outcome: "allow", INJECTED: true } };
    expect(HashChain.verifyEventHash(tampered)).toBe(false);
  });
});

describe("HashChain restoreHead", () => {
  it("restores valid hex", () => {
    const chain = new HashChain();
    const h = "a".repeat(64);
    chain.restoreHead("tnt_a", h);
    expect(chain.head("tnt_a")).toBe(h);
  });

  it("rejects invalid hex", () => {
    const chain = new HashChain();
    expect(() => chain.restoreHead("tnt_a", "not-hex")).toThrow(/invalid sha256/);
  });
});
