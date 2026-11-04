# Vouch in one container, as on Cloud Run: FastAPI serves the API under /api and the built front
# end on every other path, on the port Cloud Run gives in $PORT (8080 by default).
#
#   docker build -t vouch:dev .
#   docker run --rm -p 127.0.0.1:8081:8080 vouch:dev      # then open http://localhost:8081
#
# It runs only the scripted fake model (the bundled demo script), so it needs no secret.

# The front end: frontend/dist.
FROM node:24-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

# The backend's environment, built with uv (docs.astral.sh/uv/guides/integration/docker).
FROM python:3.12-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY pyproject.toml uv.lock ./
COPY src ./src
# Editable on purpose: the package finds fixtures/ beside src/ until M1 moves the catalogue.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

# What runs: the environment, the code, the demo company's fixtures and the build; no uv, no node.
FROM python:3.12-slim
WORKDIR /app
COPY --from=backend /app/.venv ./.venv
COPY --from=backend /app/src ./src
COPY fixtures ./fixtures
COPY --from=frontend /frontend/dist ./frontend/dist
ENV PATH="/app/.venv/bin:$PATH" VOUCH_FRONTEND=/app/frontend/dist PORT=8080
USER nobody
EXPOSE 8080
# Cloud Run's contract: listen on 0.0.0.0 at $PORT. exec, so uvicorn gets the stop signal.
CMD ["sh", "-c", "exec uvicorn vouch.api:app --host 0.0.0.0 --port \"$PORT\""]
