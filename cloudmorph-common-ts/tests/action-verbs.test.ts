import { describe, expect, it } from "vitest";
import { ACTION_VERBS, KNOWN_VERBS, verbsFor } from "../src/action-verbs.js";

describe("action-verbs", () => {
  it("locked at 21 known verbs", () => {
    expect(KNOWN_VERBS.size).toBe(21);
  });

  it("all action verb sets are subsets of KNOWN_VERBS", () => {
    for (const [action, set] of ACTION_VERBS) {
      for (const v of set) {
        expect(KNOWN_VERBS.has(v), `Action ${action} maps to unknown verb ${v}`).toBe(true);
      }
    }
  });

  it("AWS S3 list_buckets is read.list only", () => {
    expect(verbsFor("aws.s3.list_buckets")).toEqual(new Set(["read.list"]));
  });

  it("databricks.sql.execute_query is polymorphic", () => {
    const v = verbsFor("databricks.sql.execute_query");
    expect(v.has("read.list")).toBe(true);
    expect(v.has("write.update")).toBe(true);
  });

  it("unknown action returns empty set", () => {
    expect(verbsFor("unknown.action").size).toBe(0);
  });

  it("mcp.proxy.* escalation", () => {
    const proxyAll = verbsFor("mcp.proxy");
    const wrapped = verbsFor("mcp.proxy.example.com.do_thing");
    expect(wrapped.size).toBe(KNOWN_VERBS.size);
    expect(wrapped.size).toBe(proxyAll.size);
  });
});
