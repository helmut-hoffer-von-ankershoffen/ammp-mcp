# syntax=docker/dockerfile:1.7
#
# Production image for ammp-mcp.
#
# Multi-stage:
#   1. builder — uv-based, installs the project into a venv from
#                uv.lock (frozen, no dev extras, non-editable).
#   2. runtime — slim Python image; just inherits the venv from the
#                builder and runs `ammp serve` as a non-root user.
#
# Build:  make docker_build           (or `docker build -t ammp-mcp:dev .`)
# Smoke:  make docker_smoke_test
#
# Runtime state lives under AMMP_DIR. On first run `ammp serve`
# auto-bootstraps. See DEPLOYMENT.md.
#
# Pattern + uv-multi-stage approach mirror `aignostics/python-sdk`.

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.12

# ─── builder ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

# Pin uv to a specific version. Bumped together with `uv.lock` to keep
# resolver behaviour reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /uvx /bin/

# Bytecode-compile during install. Use the system interpreter (no
# `uv python install`). Copy mode so the venv survives cross-stage
# COPY into the runtime image (symlinks wouldn't).
#
# UV_PROJECT_ENVIRONMENT builds the venv directly at its final runtime
# path. Console-script shebangs are absolute and are NOT rewritten on a
# cross-stage COPY — building at /opt/ammp-venv keeps `ammp`'s shebang
# valid in the runtime image.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/ammp-venv

WORKDIR /app

# Layer-cache-friendly dep-only sync: bind-mount pyproject.toml + uv.lock
# without baking them into a layer. Re-runs only when uv.lock changes,
# not when source does.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --no-editable

# Now bring in the project source and install it non-editable. Editable
# installs skip the package-data we ship for bootstrap
# (src/ammp_mcp/_data/example_mentor/), so `--no-editable` is load-bearing.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ─── runtime ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

# OCI labels — extended at build time by docker/metadata-action in CI.
LABEL org.opencontainers.image.title="ammp-mcp" \
      org.opencontainers.image.description="Reference AMMP server (Mentoring track) on FastMCP" \
      org.opencontainers.image.source="https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp" \
      org.opencontainers.image.licenses="MIT"

# Non-root user with a real home so AMMP_DIR=$HOME/.ammp works.
# `nologin` shell + system uid range — the user is for running the
# server, not for interactive use.
#
# Create AMMP_DIR owned by `ammp` *before* the VOLUME directive below:
# Docker creates an undeclared VOLUME mountpoint as root, which would
# leave `ammp serve`'s bootstrap unable to write mentors/ on first run.
RUN <<EOT
groupadd --system --gid 1000 ammp
useradd --system --uid 1000 --gid 1000 --home-dir /home/ammp --create-home --shell /usr/sbin/nologin ammp
mkdir -p /home/ammp/.ammp
chown ammp:ammp /home/ammp/.ammp
EOT

# Drop the builder's venv in immutable mode. Owned by root, world-
# readable; the `ammp` user reads through PATH but cannot modify it.
# The builder creates it at /opt/ammp-venv directly (UV_PROJECT_ENVIRONMENT),
# so the copied console-script shebangs already resolve in this stage.
COPY --from=builder --chown=root:root --chmod=755 /opt/ammp-venv /opt/ammp-venv

ENV PATH="/opt/ammp-venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER ammp
WORKDIR /home/ammp

# Defaults overridable at runtime via `-e ...` or `--env-file`.
# `AMMP_PUBLIC_URL` is deliberately not set — operators MUST point it
# at their own domain, not at someone else's deployment.
ENV AMMP_DIR=/home/ammp/.ammp \
    AMMP_HOST=0.0.0.0 \
    AMMP_PORT=8765 \
    AMMP_REQUIRE_AUTH=true

# Persistent state — mentors/, mentees.json, audit.log, config.env.
# Mount a host directory here in production.
VOLUME ["/home/ammp/.ammp"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/.well-known/agent.json',timeout=3).status==200 else 1)"

# `ammp serve` is the auto-bootstrapping HTTP MCP server entry point.
# Was `ammp-server` before 2026-05-11; the standalone script was retired.
ENTRYPOINT ["ammp", "serve"]
