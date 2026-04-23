/**
 * Canonical JSON serialization (RFC 8785 JCS).
 *
 * Produces a byte-stable JSON representation across Python and TypeScript
 * implementations. Used as the input to the audit hash chain so that auditors
 * using either language compute identical event hashes.
 *
 * Key invariants:
 * - UTF-8 encoded (string variant) / Uint8Array (bytes variant)
 * - No whitespace
 * - Object keys sorted lexicographically
 * - Integers stay integers
 * - No NaN, no +/-Infinity
 *
 * Reference: https://www.rfc-editor.org/rfc/rfc8785
 */

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

function normalize(value: unknown): JsonValue {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "boolean" || typeof value === "string") {
    return value;
  }
  if (typeof value === "number") {
    if (Number.isNaN(value) || !Number.isFinite(value)) {
      throw new Error("canonicalJson: NaN/Infinity not permitted by RFC 8785");
    }
    // JCS §3.2.2: integers must serialize as integers
    if (Number.isInteger(value)) {
      return value;
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(normalize);
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const sortedKeys = Object.keys(obj).sort();
    const result: { [key: string]: JsonValue } = {};
    for (const key of sortedKeys) {
      result[key] = normalize(obj[key]);
    }
    return result;
  }
  throw new TypeError(`canonicalJson: unsupported type ${typeof value}`);
}

/** Returns the canonical JSON encoding as a string. */
export function canonicalJsonString(value: unknown): string {
  return JSON.stringify(normalize(value));
}

/** Returns the canonical JSON encoding as UTF-8 bytes. */
export function canonicalJson(value: unknown): Uint8Array {
  return new TextEncoder().encode(canonicalJsonString(value));
}
