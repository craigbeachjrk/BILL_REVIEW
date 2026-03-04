FROM python:3.12-slim

# System deps: git, curl, unzip for gh CLI, Node.js 22 for Claude Code
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl unzip ca-certificates gnupg && \
    # Node.js 22 (LTS) via NodeSource
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    # GitHub CLI
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y --no-install-recommends gh && \
    # AWS CLI v2 (ECS task role credentials auto-injected)
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscli.zip && \
    unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/awscli.zip /tmp/aws && \
    # Cleanup
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY agent_runner.py /app/agent_runner.py

# Non-root user (Claude Code refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash agent && chown -R agent:agent /app
USER agent

WORKDIR /app
ENTRYPOINT ["python", "agent_runner.py"]
