# Execution sandbox image. Every agent command runs in a fresh container from this image with
# --network none (except dependency installs), a read-only root filesystem, all capabilities
# dropped, no-new-privileges, CPU/memory/PID limits and a non-root user.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git make nodejs npm \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "bandit>=1.7,<2" "semgrep>=1.90,<2" "pip-audit>=2.7,<3" "pytest>=8,<9" \
 && useradd --uid 10001 --create-home sandbox

ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SEMGREP_SEND_METRICS=off
USER 10001
WORKDIR /workspace
CMD ["python", "--version"]
