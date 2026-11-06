# The Vitality modeler image. Build from the repository root:
#   docker build -f modeler/Dockerfile -t vitality-modeler .
# The lab runs it without Compose (see README.md):
#   docker run --rm --user <uid>:<gid> -v <run>/model:/model -v <run>/state:/state \
#     -v ~/.claude/.credentials.json:/home/modeler/.claude/.credentials.json \
#     -e MODELER_<SETTING>=... vitality-modeler vitality-modeler replay /model /state
# Any uid:gid works: HOME and its .claude are world-writable, and git trusts every folder.

ARG UV_VERSION=0.11.2
ARG CLAUDE_CODE_VERSION=2.1.309

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# -- build: the locked dependencies and our own packages, into a virtual environment -----------
FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
# modeler/ points at ../usermodel, so both keep their relative places.
WORKDIR /src
COPY usermodel/ usermodel/
COPY modeler/ modeler/
RUN cd modeler && uv sync --locked --no-dev --no-editable

# -- claude: Claude Code, by the native installer, pinned --------------------------------------
FROM python:3.14-slim AS claude
ARG CLAUDE_CODE_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_CODE_VERSION}
# ~/.local/bin/claude is a link into ~/.local/share/claude/versions/; keep the real file.
RUN cp -L /root/.local/bin/claude /claude

# -- final -------------------------------------------------------------------------------------
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=claude /claude /usr/local/bin/claude
COPY --from=build /opt/venv /opt/venv
ENV DISABLE_AUTOUPDATER=1 \
    HOME=/home/modeler \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
# The lab runs as the host's uid:gid, so the home and the mount points must not depend on one,
# and the mounted model repository (owned by that user) must not be refused as "dubious ownership".
RUN mkdir -p /home/modeler/.claude /model /state \
    && chmod 1777 /home/modeler /home/modeler/.claude \
    && git config --system --add safe.directory '*'
CMD ["vitality-modeler", "--help"]
