# The Vitality server image. Build from the repository root (compose.yaml does):
#   docker build -f server/Dockerfile -t vitality-server .
# Runs `vitality-server serve`; `tailscale` is here so the server can ask the host's Tailscale
# who is calling (see compose.yaml for the socket mount).

ARG UV_VERSION=0.11.2
ARG TAILSCALE_VERSION=1.104.2

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM tailscale/tailscale:v${TAILSCALE_VERSION} AS tailscale

# -- build: the locked dependencies and our own packages, into a virtual environment -----------
FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
# Use the image's own Python (never download one); keep the environment outside the source.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
# (Bytecode is compiled now: the root is read-only at run time, so Python could not cache it, and
# every healthcheck would recompile what it imports.)
# server/ points at ../protocol, so both keep their relative places.
WORKDIR /src
COPY protocol/ protocol/
COPY server/ server/
# --no-editable copies protocol and server into the environment instead of linking to /src.
RUN cd server && uv sync --locked --no-dev --no-editable

# -- final: Python, the environment, and the tailscale command --------------------------------
FROM python:3.14-slim
# The image's own standard library ships without bytecode; compile it too (see the build stage).
RUN python -m compileall -q /usr/local/lib/python3.14
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
COPY --from=build /opt/venv /opt/venv
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
CMD ["vitality-server", "serve"]
