# Base image pinned to digest for reproducible builds.
# Resolved 2026-05-14 from `docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim`.
# To refresh: docker pull python:3.12-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
# Global ARG — re-declared inside each stage that uses it.
ARG SOURCE_DATE_EPOCH

FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461 AS builder
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH
WORKDIR /build
# Upgrade pip — closes CVE-2026-6357 in default pip 26.0.1.
RUN pip install --no-cache-dir --upgrade "pip>=26.1.1"
COPY pyproject.toml README.md ./
RUN mkdir tessera && touch tessera/__init__.py
COPY tessera/ ./tessera/
RUN python -m venv /venv && \
    /venv/bin/pip install --no-cache-dir --upgrade "pip>=26.1.1" && \
    /venv/bin/pip install --no-cache-dir ".[aws,gemini,oidc,intelligence,infracost]" && \
    /venv/bin/pip install --no-cache-dir "tzdata>=2024.0"

FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH
# Upgrade runtime pip too — Tessera never invokes it but pip-audit / scanners
# inspect the image and would flag the dormant CVE.
RUN pip install --no-cache-dir --upgrade "pip>=26.1.1"
LABEL org.opencontainers.image.source="https://github.com/cloudmorphai/cloudmorph-tessera"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.title="Tessera"
LABEL org.opencontainers.image.description="The open-source MCP firewall for AI agents"

RUN groupadd -g 10001 tessera && \
    useradd  -u 10001 -g 10001 -M -s /usr/sbin/nologin tessera && \
    mkdir -p /etc/tessera/policies /var/lib/tessera && \
    chown -R tessera:tessera /etc/tessera /var/lib/tessera

COPY --from=builder /venv /venv
COPY policies/             /etc/tessera/policies-default/
COPY tessera.example.yaml  /etc/tessera/tessera.example.yaml
COPY tokens.example.yaml   /etc/tessera/tokens.example.yaml

ENV PATH="/venv/bin:$PATH"

USER tessera
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=3).status==200 else 1)"

ENV TESSERA_CONFIG_PATH=/etc/tessera/tessera.yaml
ENV TESSERA_POLICY_DIR=/etc/tessera/policies
ENV TESSERA_AUDIT_PATH=/var/lib/tessera/audit.db

CMD ["tessera", "serve"]
