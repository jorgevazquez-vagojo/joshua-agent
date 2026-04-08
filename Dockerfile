# syntax=docker/dockerfile:1
FROM python:3.12-slim

LABEL org.opencontainers.image.title="joshua-agent" \
      org.opencontainers.image.description="Autonomous gated software sprints" \
      org.opencontainers.image.source="https://github.com/jorgevazquez-vagojo/joshua-agent" \
      org.opencontainers.image.licenses="MIT"

# System deps: git (required for GitOps), curl (health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 joshua
WORKDIR /app

# Install joshua-agent from PyPI (production image)
# For local dev, override with: docker build --build-arg INSTALL_LOCAL=1
ARG INSTALL_LOCAL=0
ARG JOSHUA_VERSION=latest

COPY --chown=joshua:joshua . .

RUN if [ "$INSTALL_LOCAL" = "1" ]; then \
      pip install --no-cache-dir -e ".[all]"; \
    elif [ "$JOSHUA_VERSION" = "latest" ]; then \
      pip install --no-cache-dir "joshua-agent[all]"; \
    else \
      pip install --no-cache-dir "joshua-agent[all]==$JOSHUA_VERSION"; \
    fi

USER joshua

# Mount point for sprint configs and project files
VOLUME ["/workspace"]
WORKDIR /workspace

# Default: show help. Override with: docker run ... joshua run sprint.yaml
ENTRYPOINT ["joshua"]
CMD ["--help"]
