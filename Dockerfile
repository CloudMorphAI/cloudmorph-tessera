FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
RUN mkdir tessera && touch tessera/__init__.py
COPY tessera/ ./tessera/
RUN pip install --target=/install --no-cache-dir . && \
    pip install --target=/install --no-cache-dir "tzdata>=2024.0"

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/cloudmorph-ai/cloudmorph-tessera"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.title="Tessera"
LABEL org.opencontainers.image.description="The open-source MCP firewall for AI agents"

RUN groupadd -g 10001 tessera && \
    useradd  -u 10001 -g 10001 -M -s /usr/sbin/nologin tessera && \
    mkdir -p /etc/tessera/policies /var/lib/tessera && \
    chown -R tessera:tessera /etc/tessera /var/lib/tessera

COPY --from=builder /install /usr/local/lib/python3.12/site-packages
COPY policies/             /etc/tessera/policies-default/
COPY tessera.example.yaml  /etc/tessera/tessera.example.yaml
COPY tokens.example.yaml   /etc/tessera/tokens.example.yaml

USER tessera
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=3).status==200 else 1)"

ENV TESSERA_CONFIG_PATH=/etc/tessera/tessera.yaml
ENV TESSERA_POLICY_DIR=/etc/tessera/policies
ENV TESSERA_AUDIT_PATH=/var/lib/tessera/audit.db

CMD ["tessera", "serve"]
