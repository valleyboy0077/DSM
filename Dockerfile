# syntax=docker/dockerfile:1

# Build the Vite bundle into the path served by FastAPI.
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app
COPY frontend/package.json ./frontend/package.json
RUN cd frontend && npm install
COPY frontend ./frontend
COPY src/dsm ./src/dsm
RUN cd frontend && npm run build

# Build wheels once so the runtime image needs no compiler or package manager.
FROM python:3.11-slim AS python-build
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DSM_DB_PATH=/var/lib/dsm/dsm.db \
    DSM_HOST=0.0.0.0 \
    DSM_PORT=8080

RUN addgroup --system dsm && adduser --system --ingroup dsm --home /app dsm
WORKDIR /app
COPY --from=python-build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --from=python-build /app/src ./src
COPY --from=frontend-build /app/src/dsm/frontend ./src/dsm/frontend

RUN mkdir -p /var/lib/dsm && chown -R dsm:dsm /app /var/lib/dsm
USER dsm

EXPOSE 8080 8101
CMD ["uvicorn", "dsm.app:app", "--host", "0.0.0.0", "--port", "8080"]
