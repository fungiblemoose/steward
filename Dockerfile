# ---- Stage 1: build the frontend SPA ----
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user
RUN useradd --create-home --uid 10001 steward

WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/steward ./steward
RUN pip install --no-cache-dir .

# Built SPA lands where the API expects it: /app/frontend/dist
COPY --from=frontend /build/dist /app/frontend/dist

# Data dir for the SQLite db (mount a volume here in production)
RUN mkdir -p /app/data && chown -R steward:steward /app
USER steward

ENV STEWARD_HOST=0.0.0.0 \
    STEWARD_PORT=8080 \
    STEWARD_DB_PATH=/app/data/steward.db

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/health').status==200 else 1)"

CMD ["python", "-m", "steward"]
