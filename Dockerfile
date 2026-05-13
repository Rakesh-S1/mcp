# ── Stage 1: build deps ──────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user — never run containers as root
RUN addgroup --system mcp && adduser --system --ingroup mcp mcpuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY mcp_server/ ./mcp_server/

# Azure Container Apps injects PORT env var (default 8000)
ENV PORT=8000 \
    MCP_TRANSPORT=http \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER mcpuser

EXPOSE 8000

# uvicorn serves the ASGI http_app with Azure AD middleware attached
CMD ["python", "-m", "uvicorn", "mcp_server.server:http_app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
