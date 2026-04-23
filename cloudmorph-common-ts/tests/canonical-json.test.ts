/**
 * Canonical-JSON tests; cross-checked against the Python suite at
 * cloudmorph-common-py/tests/test_canonical_json.py — both languages MUST
 * compute identical SHA-256 hashes for the same input.
 */

import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { canonicalJson, canonicalJsonString } from "../src/audit/canonical-json.js";

describe("canonicalJson basics", () => {
  it("empty object", () => {
    expect(canonicalJsonString({})).toBe("{}");
  });
  it("empty array", () => {
    expect(canonicalJsonString([])).toBe("[]");
  });
  it("null", () => {
    expect(canonicalJsonString(null)).toBe("null");
  });
  it("booleans", () => {
    expect(canonicalJsonString(true)).toBe("true");
    expect(canonicalJsonString(false)).toBe("false");
  });
  it("string", () => {
    expect(canonicalJsonString("hello")).toBe('"hello"');
  });
  it("integer", () => {
    expect(canonicalJsonString(42)).toBe("42");
    expect(canonicalJsonString(0)).toBe("0");
    expect(canonicalJsonString(-7)).toBe("-7");
  });
  it("float with integer value", () => {
    expect(canonicalJsonString(5.0)).toBe("5");
  });
  it("float real", () => {
    expect(canonicalJsonString(1.5)).toBe("1.5");
  });
});

describe("canonicalJson ordering", () => {
  it("sorts object keys", () => {
    expect(canonicalJsonString({ b: 2, a: 1, c: 3 })).toBe('{"a":1,"b":2,"c":3}');
  });
  it("preserves array order", () => {
    expect(canonicalJsonString([3, 1, 2])).toBe("[3,1,2]");
  });
  it("recursively sorts nested objects", () => {
    expect(canonicalJsonString({ z: { y: 1, x: 2 }, a: 1 })).toBe('{"a":1,"z":{"x":2,"y":1}}');
  });
});

describe("canonicalJson rejections", () => {
  it("rejects NaN", () => {
    expect(() => canonicalJsonString(NaN)).toThrow(/NaN\/Infinity/);
  });
  it("rejects Infinity", () => {
    expect(() => canonicalJsonString(Infinity)).toThrow(/NaN\/Infinity/);
  });
  it("rejects -Infinity", () => {
    expect(() => canonicalJsonString(-Infinity)).toThrow(/NaN\/Infinity/);
  });
});

describe("canonicalJson SHA-256 stability (cross-language)", () => {
  // These hashes MUST match cloudmorph-common-py/tests/test_canonical_json.py exactly.
  const cases: { value: unknown; expected: string }[] = [
    { value: {}, expected: "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a" },
    { value: [], expected: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945" },
    { value: { a: 1, b: 2 }, expected: "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777" },
  ];
  for (const { value, expected } of cases) {
    it(`hash matches for ${JSON.stringify(value)}`, () => {
      const h = createHash("sha256").update(canonicalJson(value)).digest("hex");
      expect(h).toBe(expected);
    });
  }
});
