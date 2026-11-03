# The Vitality observer image. Build from the repository root (compose.yaml does):
#   docker build -f observer/Dockerfile -t vitality-observer .
# Runs `vitality-observer run`, which sends each interaction's screenshots to the Anthropic API
# (the key comes from the environment, see compose.yaml).

ARG UV_VERSION=0.11.2

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# -- build: the locked dependencies and our own package, into a virtual environment ------------
FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
# (Bytecode is compiled now: the root is read-only at run time, so Python could not cache it.)
# observer/ points at ../protocol and ../usermodel, so all three keep their relative places.
WORKDIR /src
COPY protocol/ protocol/
COPY usermodel/ usermodel/
COPY observer/ observer/
# --no-editable copies protocol, usermodel and observer into the environment instead of linking to /src.
RUN cd observer && uv sync --locked --no-dev --no-editable

# -- final -------------------------------------------------------------------------------------
FROM python:3.14-slim
# The image's own standard library ships without bytecode; compile it too (see the build stage).
RUN python -m compileall -q /usr/local/lib/python3.14
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
COPY --from=build /opt/venv /opt/venv
# The mount points (compose.yaml mounts the data folders over them).
RUN mkdir -p /captures /model/observations /model/operations /state
CMD ["vitality-observer", "run"]
