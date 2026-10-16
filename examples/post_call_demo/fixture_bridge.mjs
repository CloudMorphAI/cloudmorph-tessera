// Private stdin/stdout bridge. Never expose the policy-agnostic adapter over HTTP.
import { createInterface } from "node:readline";
import { createPostCallFixture } from "./backend/index.mjs";

const fixture = createPostCallFixture({ run_id: process.argv[2], starting_discount_pct: 0 });
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  try {
    if (Buffer.byteLength(line) > 32768) throw new Error("oversized request");
    const request = JSON.parse(line);
    let result;
    if (request.op === "catalog") result = fixture.get_tool_catalog();
    else if (request.op === "snapshot") result = fixture.snapshot();
    else if (request.op === "dispatch") result = fixture.dispatch(request.invocation);
    else throw new Error("unknown private operation");
    process.stdout.write(JSON.stringify({ ok: true, result }) + "\n");
  } catch {
    process.stdout.write(JSON.stringify({ ok: false, error: "Local fixture bridge failed" }) + "\n");
  }
}
