# syntax=docker/dockerfile:1.7
#
# Production image for ammp-mcp. Multi-stage:
#
#   1. builder  — uv-based, installs the project into a venv from
#                 uv.lock (frozen, no dev extras, non-editable).
#   2. runtime  — slim Python image; just inherits the venv from the
#                 builder and runs `ammp serve` as a non-root user.
#
# Runtime state lives under AMMP_DIR. On first run `ammp serve`
# auto-bootstraps: creates AMMP_DIR, copies the packaged example
# mentor in, writes a minimal config.env. For real deployments,
# mount your own state at AMMP_DIR — see DEPLOYMENT.md.

# ─── builder ─────────────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /build

# Bytecode-compile on install for slightly faster cold starts;
# copy-link mode so the venv is self-contained for the COPY below.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Resolve the dep tree first against uv.lock alone so layer cache is
# reusable across pure source-only changes.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

# Now bring in the project and install it non-editable into the venv.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ─── runtime ─────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Non-root user with a real home so AMMP_DIR=$HOME/.ammp works.
RUN useradd --system --uid 1000 --create-home --home-dir /home/ammp --shell /bin/false ammp

# Drop the builder's venv straight into a stable runtime path. The
# package's `_data/example_mentor/` rides along inside this venv as
# real installed files (non-editable) — the bootstrap helper finds
# them via importlib.resources.
COPY --from=builder /build/.venv /opt/ammp-venv

ENV PATH="/opt/ammp-venv/bin:$PATH"

USER ammp
WORKDIR /home/ammp

ENV AMMP_DIR=/home/ammp/.ammp \
    AMMP_HOST=0.0.0.0 \
    AMMP_PORT=8765 \
    AMMP_REQUIRE_AUTH=true \
    AMMP_PUBLIC_URL=https://ammp.helmguild.com

# Persistent state — mentors, mentee allowlist, audit log, config.env.
# Mount a host directory here in production.
VOLUME ["/home/ammp/.ammp"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/.well-known/agent.json',timeout=3).status==200 else 1)"

# `ammp serve` is the (auto-bootstrapping) HTTP MCP server entry point.
# Was `ammp-server` before 2026-05-11; the standalone script was retired.
ENTRYPOINT ["ammp", "serve"]
