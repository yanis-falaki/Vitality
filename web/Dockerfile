# The Vitality web image. Build from the repository root (compose.yaml does):
#   docker build -f web/Dockerfile -t vitality-web .
# Runs `vitality-web serve`: the read-only API and the built app, on port 4000. The model, the
# modeler's records and the captures are mounted read-only at /model, /state and /captures.

ARG UV_VERSION=0.11.2
ARG NODE_VERSION=22.21.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# -- app: the React app, built into static files with its locked dependencies ------------------
FROM node:${NODE_VERSION}-slim AS app
WORKDIR /app
COPY web/app/package.json web/app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/app/ ./
RUN npm run build

# -- build: the locked Python dependencies and our own packages, into a virtual environment ----
FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
# web/ points at ../usermodel, so both keep their relative places.
WORKDIR /src
COPY usermodel/ usermodel/
COPY web/pyproject.toml web/uv.lock web/
COPY web/vitality_web/ web/vitality_web/
RUN cd web && uv sync --locked --no-dev --no-editable

# -- final: Python, the environment, the built app, and git (to count the operations waiting) --
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m compileall -q /usr/local/lib/python3.14
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
COPY --from=build /opt/venv /opt/venv
COPY --from=app /app/dist /opt/vitality-web/app
# The mounted model repository belongs to the data folder's owner: git must not refuse it.
RUN git config --system --add safe.directory '*' && mkdir -p /model /state /captures
EXPOSE 4000
CMD ["vitality-web", "serve"]
