# CloudMorph Control Centre — root Makefile
# All targets idempotent. Each target prints what it's doing.

.PHONY: help contracts contracts-verify lint lint-py lint-ts lint-rego \
        test test-py test-ts test-rego test-adversarial \
        bench docker-mcp docker-executors docker-all install-dev clean

help:
	@echo "CloudMorph Control Centre — make targets"
	@echo ""
	@echo "  contracts          Generate Pydantic + TS types from contracts/*.schema.json"
	@echo "  contracts-verify   Regenerate and assert no diff (CI gate)"
	@echo "  lint               All linters (Python + TS + Rego + JSON Schema)"
	@echo "  test               All tests (Python + TS + Rego)"
	@echo "  test-adversarial   Run only adversarial fixtures"
	@echo "  bench              Run autocannon perf harness against MCP"
	@echo "  docker-mcp         Build cloudmorph-mcp container"
	@echo "  docker-executors   Build all 5 executor containers"
	@echo "  docker-all         Build everything"
	@echo "  install-dev        Install dev deps (pre-commit, pytest, vitest, etc.)"
	@echo "  clean              Remove generated artifacts"

CONTRACTS_PY := cloudmorph-common-py/cloudmorph_common/contracts
CONTRACTS_TS := cloudmorph-common-ts/src/contracts

contracts:
	@echo "→ Generating Pydantic models …"
	@mkdir -p $(CONTRACTS_PY) $(CONTRACTS_TS)
	@for f in contracts/*.schema.json; do \
	  name=$$(basename $$f .schema.json | tr '-' '_'); \
	  datamodel-codegen \
	    --input $$f \
	    --input-file-type jsonschema \
	    --output $(CONTRACTS_PY)/$$name.py \
	    --output-model-type pydantic_v2.BaseModel \
	    --use-schema-description \
	    --target-python-version 3.9 \
	    --use-standard-collections \
	    --use-union-operator || exit 1; \
	done
	@touch $(CONTRACTS_PY)/__init__.py
	@echo "→ Generating TypeScript interfaces …"
	@for f in contracts/*.schema.json; do \
	  name=$$(basename $$f .schema.json); \
	  json2ts -i $$f -o $(CONTRACTS_TS)/$$name.ts || exit 1; \
	done
	@echo "✓ Contracts generated to $(CONTRACTS_PY) and $(CONTRACTS_TS)"

contracts-verify: contracts
	@echo "→ Verifying generated artifacts match committed state …"
	@git diff --exit-code $(CONTRACTS_PY) $(CONTRACTS_TS) \
	  || (echo "✗ Generated files out of sync — run 'make contracts' and commit." && exit 1)
	@echo "✓ Contracts in sync"

lint: lint-py lint-ts lint-rego

lint-py:
	@echo "→ ruff check …"
	@ruff check . || exit 1
	@echo "→ ruff format --check …"
	@ruff format --check . || exit 1
	@echo "→ mypy --strict …"
	@mypy --strict cloudmorph-common-py/ sdk-python/ || exit 1
	@echo "✓ Python lint clean"

lint-ts:
	@echo "→ eslint …"
	@cd cloudmorph-mcp && npm run lint || exit 1
	@echo "✓ TS lint clean"

lint-rego:
	@echo "→ opa fmt --diff …"
	@if [ -d cloudmorph-mcp/test-fixtures/bundles ]; then \
	  find cloudmorph-mcp/test-fixtures/bundles -name '*.rego' -exec opa fmt --diff {} \; ; \
	fi

test: test-py test-ts test-rego

test-py:
	@echo "→ pytest …"
	@pytest tests/ cloudmorph-common-py/tests/ sdk-python/tests/ -v --cov

test-ts:
	@echo "→ vitest …"
	@cd cloudmorph-mcp && npm test

test-rego:
	@echo "→ opa test …"
	@if [ -d cloudmorph-mcp/src/policy/rules ]; then \
	  opa test cloudmorph-mcp/src/policy/rules/ --coverage; \
	else \
	  echo "(no Rego rules yet)"; \
	fi

test-adversarial:
	@echo "→ adversarial fixtures …"
	@pytest tests/adversarial/ -v

bench:
	@echo "→ autocannon MCP perf harness …"
	@cd cloudmorph-mcp && npm run bench

docker-mcp:
	@echo "→ docker build cloudmorph-mcp …"
	@docker buildx build \
	  --platform linux/amd64,linux/arm64 \
	  -f cloudmorph-mcp/Dockerfile \
	  -t ghcr.io/cloudmorphai/cloudmorph-mcp:dev \
	  cloudmorph-mcp/

docker-executors:
	@for cloud in aws azure gcp databricks snowflake; do \
	  echo "→ docker build $$cloud-executor …"; \
	  docker buildx build \
	    --platform linux/amd64,linux/arm64 \
	    -f $$cloud/executor/Dockerfile \
	    -t ghcr.io/cloudmorphai/$$cloud-executor:dev \
	    . || exit 1; \
	done

docker-all: docker-mcp docker-executors

install-dev:
	@echo "→ pip install dev tools …"
	@pip install pre-commit ruff mypy pytest pytest-cov pydantic datamodel-code-generator
	@npm install -g json-schema-to-typescript
	@echo "→ pre-commit install …"
	@pre-commit install
	@echo "✓ Dev environment ready"

clean:
	@echo "→ Removing generated artifacts …"
	@rm -rf $(CONTRACTS_PY) $(CONTRACTS_TS)
	@find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@find . -name '.pytest_cache' -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.egg-info' -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Clean"
