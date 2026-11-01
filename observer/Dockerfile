# The Vitality observer image. Build from the repository root (compose.yaml does):
#   docker build -f observer/Dockerfile -t vitality-observer .
# Runs `vitality-observer run`, which calls Claude Code (`claude -p`) on each batch of captures.

ARG UV_VERSION=0.11.2
ARG CLAUDE_CODE_VERSION=2.1.309

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
WORKDIR /src
COPY observer/ observer/
RUN cd observer && uv sync --locked --no-dev --no-editable

# -- claude: Claude Code from the official installer, with a throwaway home --------------------
FROM python:3.14-slim AS claude
ARG CLAUDE_CODE_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
# The installer puts a symlink in $HOME/.local/bin; copy what it points at (cp -L) out of the throwaway home.
RUN export HOME=/tmp/installer-home && mkdir -p "$HOME" \
    && curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}" \
    && cp -L "$HOME/.local/bin/claude" /usr/local/bin/claude \
    && /usr/local/bin/claude --version

# -- final -------------------------------------------------------------------------------------
FROM python:3.14-slim
# imagemagick: lets Claude crop and zoom into screenshots (webp included).
RUN apt-get update \
    && apt-get install -y --no-install-recommends imagemagick \
    && rm -rf /var/lib/apt/lists/*
# The image's own standard library ships without bytecode; compile it too (see the build stage).
RUN python -m compileall -q /usr/local/lib/python3.14
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/observer \
    DISABLE_AUTOUPDATER=1
COPY --from=build /opt/venv /opt/venv
COPY --from=claude /usr/local/bin/claude /usr/local/bin/claude
CMD ["vitality-observer", "run"]
