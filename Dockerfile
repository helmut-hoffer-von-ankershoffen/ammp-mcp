FROM python:3.13-slim AS builder

WORKDIR /build

# Install build deps and the package itself.
COPY pyproject.toml LICENSE README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install .

# ─── runtime ─────────────────────────────────────────────────────────────
FROM python:3.13-slim

# Non-root user — the server doesn't need root.
RUN useradd --system --uid 1000 --create-home pepe
WORKDIR /srv/ammp-mcp

COPY --from=builder /install /usr/local
COPY mentors/ ./mentors/
# A live deployment mounts mentees.json + audit.log as a volume; we ship
# only the example so a fresh container won't accidentally start without
# an explicit allowlist.
COPY mentees.example.json ./mentees.example.json

USER pepe

ENV AMMP_HOST=0.0.0.0 \
    AMMP_PORT=8765 \
    AMMP_MENTORS_ROOT=/srv/ammp-mcp/mentors \
    AMMP_MENTEES_FILE=/srv/ammp-mcp/mentees.json \
    AMMP_AUDIT_LOG_PATH=/srv/ammp-mcp/audit.log \
    AMMP_REQUIRE_AUTH=true \
    AMMP_PUBLIC_URL=https://ammp.helmguild.com

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/.well-known/agent.json',timeout=3).status==200 else 1)"

ENTRYPOINT ["ammp-server"]
