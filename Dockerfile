# Multi-stage build for mnq_bot trading system
# Stage 1: Build with uv and dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src src/

# Install dependencies using uv
RUN /root/.cargo/bin/uv pip install --python /usr/local/bin/python3.12 \
    --no-cache-dir --compile-bytecode .

# Stage 2: Runtime image
FROM python:3.12-slim

LABEL org.opencontainers.image.title="mnq-bot"
LABEL org.opencontainers.image.description="Self-learning MNQ scalping system with Tradovate execution"
LABEL org.opencontainers.image.version="0.0.1"
LABEL org.opencontainers.image.vendor="mnq"

# Create non-root user
RUN useradd -m -u 10001 mnq

WORKDIR /app

# Copy installed packages from builder (only site-packages and scripts)
COPY --from=builder --chown=mnq:mnq /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder --chown=mnq:mnq /usr/local/bin /usr/local/bin

# Copy source code
COPY --chown=mnq:mnq src src/

# Create runtime directories
RUN mkdir -p /var/lib/mnq-bot /var/log/mnq-bot && \
    chown -R mnq:mnq /var/lib/mnq-bot /var/log/mnq-bot

# Switch to non-root user
USER mnq

# Expose Prometheus metrics port
EXPOSE 9108

# Health check using doctor command
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -m mnq.cli.main doctor --json || exit 1

# Set PYTHONPATH to include src
ENV PYTHONPATH=/app/src:$PYTHONPATH
ENV PYTHONUNBUFFERED=1

# Default command: run health check
CMD ["doctor"]

# Entry point: allows flexible subcommands
ENTRYPOINT ["python", "-m", "mnq.cli.main"]
