# LLM Policy Authoring

The opt-in subsystem that lets a user say "here is my MCP tool catalog (or my free-text intent), generate Tessera policies for it." Lives at `tessera/llm/`. This is **not** the firewall hot path. Policies generated here are YAML drafts that a human reviews and commits to `policies/` before they ever run through the engine.

## Architectural property: invocation-time-optional

The firewall does not depend on this subsystem at runtime. No `tools/call` in production ever invokes an LLM provider. The hot path is `tessera.proxy` → `tessera.policy.engine` → static YAML files; nothing in that path can call out to Anthropic, OpenAI, Google, AWS Bedrock, or Azure OpenAI.

This separation is load-bearing for two properties:

1. **No firewall-hot-path latency budget concerns.** A 2-second LLM call is fine for policy authoring (the user is waiting for a draft); 2-second LLM calls inside `tools/call` would make Tessera unusable. Keeping LLM out of the engine guarantees the deterministic-block-at-call-time wedge described in `proxy-enforcement-and-audit.md`.
2. **No LLM-provider-outage blast radius on the firewall itself.** If every LLM provider is down, the firewall continues operating on whatever policies are on disk. The only impact is that a user invoking `tessera policy author` or `tessera analyze` gets an error and tries again later.

The subsystem is reachable through two CLI surfaces and direct programmatic use:

- `tessera policy author --intent "..." --model gemini` — generate from free-text intent.
- `tessera analyze --mcp <url> --model gemini` — connect to a live MCP server, fetch its tool catalog (`tools/list`), generate policies for the surface.
- Programmatic: `from tessera.llm.gemini import GeminiPolicyAuthor; GeminiPolicyAuthor().propose_policies("...")`.

Each path emits draft YAMLs prefixed with the comment `# Generated draft — review before deploying.` The output is never auto-loaded into `policies.dir`; the user copies the YAMLs in deliberately.

## The PolicyAuthor + ToolCatalogAnalyzer interfaces

`tessera/llm/base.py` defines two `Protocol` classes that every provider implements:

```python
class PolicyAuthor(Protocol):
    def propose_policies(self, intent: str, condition_catalog: dict | None = None,
                         max_retries: int = 3) -> list[PolicyRecommendation]: ...

class ToolCatalogAnalyzer(Protocol):
    def analyze_tools(self, tools: list[dict], upstream_name: str | None = None
                      ) -> list[PolicyRecommendation]: ...
```

The output unit is `PolicyRecommendation(filename, reason, yaml_body)`. The provider returns a list; the CLI either writes each to disk under `--output <dir>` or echoes them to stdout. The YAML bodies are validated against `Policy.model_validate(yaml.safe_load(...))` before being returned — a malformed YAML body is treated as a retryable LLM error, fed back into the next attempt's prompt as "Previous attempt produced invalid YAML: <error>. Fix and try again." Up to 3 retries by default. After exhaustion, an empty list is returned and a warning is logged.

Returning an empty list is the design choice for "I tried and couldn't produce valid output," rather than raising. The CLI handles the empty case explicitly with "No policies generated." This matches the rest of the codebase's fail-direction convention: silent degradation over loud crashes for non-critical paths.

## Schema-driven system prompt

`tessera/llm/_shared.py:build_system_prompt()` is the prompt builder. Critically, it introspects `Policy.model_json_schema()` at runtime and emits the catalog of condition types directly from the live schema — not from a hand-maintained string. When a new condition is added to `tessera/policy/schema.py`, the system prompt automatically picks it up; there is no second registry to keep in sync.

The prompt structure:

1. **Policy schema description** — extracted from `Policy.model_json_schema().properties`. Field names + types + descriptions.
2. **Match block usage** — a static example showing `upstream`, `tool`, `tool_pattern`, `require_intent`.
3. **Action values** — the four enum values.
4. **Condition types** — auto-generated. For each subclass in the discriminated union, the prompt lists the discriminator value and the available fields. The current 21 conditions appear here without any per-condition prompt maintenance.
5. **Five hand-written examples** — block-all-deletes, block-prod-writes, require-approval-large-transfer, region-lockdown, business-hours-only. These exist because LLMs benefit from concrete examples for the YAML-output discipline (correct indentation, full match+when+action triple, valid priority).
6. **Output format** — JSON array of `{filename, reason, yaml_body}`.

The shared builder is consumed by every provider's `__init__`. There is no per-provider variation of the system prompt in v0.2.0; if a provider needs a tweak it would specialize the builder. Today none do.

## The 5 v0.2.0 provider implementations

Each provider is a single Python file at `tessera/llm/<name>.py` and implements both `PolicyAuthor` and `ToolCatalogAnalyzer`:

- **`gemini.py:GeminiPolicyAuthor`** — uses `google.genai`. Default model `gemini-2.0-flash-exp`. Sets `response_mime_type="application/json"` so the SDK returns structured JSON natively. `temperature=0.1`, `top_k=20` for low-variance output. **This is the v0.2.0 primary** — the production-tested provider. Other providers are "Alpha. v0.2.0 stub — production-tested only against Gemini" (per the docstring in `anthropic.py`).
- **`anthropic.py:AnthropicPolicyAuthor`** — uses `anthropic`. Default model `claude-3-5-sonnet-20241022`. Standard messages API with the system prompt and a single user message.
- **`openai.py:OpenAIPolicyAuthor`** — uses `openai`. The OpenAI chat-completions API.
- **`azure_openai.py:AzureOpenAIPolicyAuthor`** — uses `openai` + `azure-identity`. Same chat-completions interface, Azure-routed.
- **`bedrock.py:BedrockPolicyAuthor`** — uses `boto3` to call Bedrock's `InvokeModel`. The model defaults to a Claude variant available through Bedrock.

Each provider is behind a pip extras group (`[anthropic]`, `[openai]`, `[bedrock]`, `[azure-openai]`, `[gemini]`, or the `[all-llm]` aggregate). The default `pip install cloudmorph-tessera` brings in none of them. The Docker image ships with `[aws,gemini,oidc,intelligence,infracost]` extras pre-installed (see the Dockerfile at line 18), so the container has Gemini available by default but not the other four.

## Why these 5 specifically

The provider set was chosen as a cost-and-coverage decision, not a technical preference for any model family:

- **Gemini** — primary because the founder's funding stack targets Gemini API credits aggressively, and `gemini-2.0-flash` is the cheapest credible model in the production-tested provider set.
- **Anthropic + OpenAI** — the two dominant enterprise-default providers. Customer expectations are that "the LLM in my stack" is one of these two.
- **Bedrock** — for AWS-native customers who can't (or won't) send tool catalogs to a non-AWS endpoint. Cost-bills against the same AWS account that already pays for the firewall.
- **Azure OpenAI** — same logic, Azure-native customers.

Mistral and Cohere are deliberately out of v0.2.0 scope. They are credible alternatives — Mistral on EU-resident-data grounds, Cohere on cost-per-token for enterprise — but adding them requires implementing two more provider classes, two more SDK dependencies, and (more importantly) two more rounds of "how does this provider handle structured JSON output." The founder's funding stack has Mistralship (€30K cohort) and Cohere startup program (~25% off enterprise) in the AI-credit acquisition pipeline; integration is captured as `improvements/v0.4.0-llm-providers-mistral-cohere.md`. Adding them after the credit footprint is acquired matches the cost-discipline pattern that drove the original 5.

## Cost-discipline default

Every provider class accepts a `model: str` parameter with a hardcoded default. The defaults are chosen to be the cheapest credible model per provider that produces valid YAML at low variance. Users with stronger requirements override at instantiation time. The CLI `--model` flag selects the provider; selecting a non-default model within a provider requires a programmatic override.

Today this discipline is implicit in the source. If/when the `tessera policy author` CLI grows a `--model-name` flag (vs `--model` for the provider), the cost-discipline default becomes explicit.

## Retry-on-invalid-YAML pattern

Each provider's `propose_policies` and `analyze_tools` share a retry loop:

```python
last_error = None
for attempt in range(max_retries):
    prompt = base_prompt
    if last_error:
        prompt += f"\n\nPrevious attempt produced invalid YAML: {last_error}. Fix and try again."
    try:
        response = <provider sdk call>(...)
        return self._parse_and_validate_response(response.text)
    except Exception as exc:
        last_error = str(exc)
        logger.warning("event=<provider>_propose_retry attempt=%d/%d error=%s", attempt+1, max_retries, exc)
return []  # exhausted
```

The pattern works regardless of SDK; the error-feedback loop is in plain string-concatenation territory. Default `max_retries=3` because three attempts is the empirically-tested sweet spot where most provider-side errors are correctable (JSON-wrapping issues, condition-name typos, missing required fields). Beyond three, retrying tends to repeat the same mistake.

The retry doesn't pay for a fresh tool catalog or fresh intent description; the input is identical, only the error-feedback suffix changes. This is good enough for the policy-authoring use case (the operator is interactive and can run the CLI again with a refined intent if three retries produce nothing) but would be insufficient for a production-loop use case (a hot loop would just keep failing).

## Tool catalog discovery via `tessera analyze`

`tessera analyze --mcp <url>` calls `tools/list` against the supplied MCP server URL, extracts the `result.tools` array, derives an upstream name from the URL netloc, and hands `(tools, upstream_name)` to the chosen provider's `analyze_tools`. The provider's prompt extension is "Analyze the following MCP tool catalog for upstream '<name>' and recommend Tessera firewall policies to enforce least-privilege access: <tools JSON>".

This is the "I just installed a new MCP server, what should my policy set look like?" workflow. Output is per-tool block / require_approval / allow recommendations, packaged as `PolicyRecommendation` entries. The human reviewer decides which to commit; the LLM does not enable the policies automatically.

## Where this subsystem doesn't fit

The `_shared.build_system_prompt()` is a single function. The 5 provider classes share that prompt verbatim; no per-provider scaffolding exists today. If a provider needs structural differences (e.g., Mistral's tokenizer requiring tighter prompt budget, Cohere's structured-output API differing from OpenAI's), the prompt builder gets a provider-arg parameter. Today it has none.

Per-condition prompt guidance — "for `predicted_cost`, prefer 'high' band when the operator is concerned about underestimating; prefer 'ceiling' for Bedrock" — does not appear in the prompt. The schema lists the fields; the LLM picks values based on the example block. Adding richer per-condition guidance is a prompt-engineering exercise that hasn't been needed in practice: Gemini's schema-driven outputs have been good enough that the bake-time gains aren't yet worth the prompt-bloat cost.

## Cross-references

- For where authored policies eventually land (the engine they're validated against): `policy-engine.md`.
- For the v0.4.0 expansion to Mistral and Cohere: `improvements/v0.4.0-llm-providers-mistral-cohere.md`.
- For the CLI surface (`tessera policy author`, `tessera analyze`): `tessera/cli.py:policy_author` and `tessera/cli.py:analyze`.
