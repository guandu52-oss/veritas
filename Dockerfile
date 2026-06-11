# Stage 1: Build frontend
FROM node:22-alpine AS frontend-builder

WORKDIR /build
COPY web/frontend/package*.json ./
RUN npm ci --silent

COPY web/frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM node:22-bookworm-slim

# Install Python and system dependencies for static audit/image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    libgl1 \
    libglib2.0-0 \
    python3 \
    python3-pip \
    python3-venv \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ARG OPENCODE_NPM_PACKAGE=opencode-ai@latest
RUN npm install -g "${OPENCODE_NPM_PACKAGE}"

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV VERITAS_HOST=0.0.0.0
ENV VERITAS_PORT=8765
ENV VERITAS_DATA_ROOT=/app/web_data
ENV VERITAS_OUTPUT_ROOT=/app/outputs
ENV VERITAS_OPENCODE_BIN=opencode

WORKDIR /app

# Install Python dependencies (from pyproject.toml)
COPY pyproject.toml .
COPY cli/ ./cli/
COPY engine/ ./engine/
COPY protocols/ ./protocols/
COPY runtime/ ./runtime/
COPY configs/ ./configs/
COPY web/backend/ ./web/backend/
COPY scripts/ ./scripts/
COPY opencode.json .
COPY .opencode/skills/ ./.opencode/skills/
COPY README.md .
RUN python3 -m venv "${VIRTUAL_ENV}" \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Copy frontend build artifacts from stage 1
COPY --from=frontend-builder /build/dist ./web/frontend/dist

# Create data directories
RUN mkdir -p /app/web_data /app/outputs

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV VERITAS_HOST=0.0.0.0
ENV VERITAS_PORT=8765

# Set timezone
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

# Expose port and start command
EXPOSE 8765
CMD ["python", "-m", "web.backend.veritas_web.app"]
