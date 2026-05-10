# Contributing to cloudmorph-mcp

Thank you for your interest in contributing! This document describes the process for reporting bugs, requesting features, and submitting pull requests.

---

## Getting Started

### Prerequisites

- Node.js 18+
- npm 9+
- Docker (optional, for container testing)

### Local setup

```bash
git clone https://github.com/CloudMorphAI/cloudmorph-mcp.git
cd cloudmorph-mcp
npm install
cp .env.example .env   # fill in CONTROL_CENTER_API_URL at minimum
npm run build
npm start
```

Verify the server is running:

```bash
curl http://localhost:8080/health
# → {"status":"ok"}
```

---

## Development Workflow

### Branch naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feat/<short-description>` | `feat/streaming-events` |
| Bug fix | `fix/<short-description>` | `fix/ws-token-leak` |
| Docs | `docs/<short-description>` | `docs/cursor-mcp-config` |
| Chore | `chore/<short-description>` | `chore/bump-express` |

### Making changes

1. Fork the repository and create your branch from `main`.
2. Make your changes. Keep commits focused and atomic.
3. Run the checks locally before pushing:

```bash
npm run lint    # TypeScript type-check
npm run build   # Compile to dist/
npm test        # Run tests
```

4. Open a pull request against `main` with a clear description of what changed and why.

---

## Pull Request Guidelines

- **One concern per PR** — avoid bundling unrelated changes.
- **Update `.env.example`** if you add a new environment variable.
- **Update `README.md`** if you add/change a public-facing behaviour.
- **Keep backward compatibility** — removing or renaming env vars requires a major version bump.
- All CI checks must pass before a PR can be merged.

---

## Reporting Issues

Use the GitHub issue templates:

- **Bug report** — something isn't working as documented.
- **Feature request** — an idea for a new capability.

Please search existing issues before opening a new one.

---

## Code Style

- TypeScript strict mode is enforced — do not disable it.
- No external runtime dependencies beyond `express` and `ws` without discussion.
- Structured JSON logging only — no `console.log` outside of startup messages.
- All environment variables must be documented in `.env.example`.

---

## Security

If you discover a security vulnerability, **do not open a public issue**. Instead, email security@cloudmorph.ai with a description of the issue and steps to reproduce. We will respond within 48 hours.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
