# FastAPI app container for AWS App Runner
# Use ECR Public mirror to avoid Docker Hub rate limits in CodeBuild
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# System deps (ssl, curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy entire context (handles cases where Windows ZIP preserved backslashes in names)
COPY . ./

# Normalize any Windows-style filenames like 'templates\\day.html' into a real templates/ directory (no heredoc for compatibility)
RUN python -c "import os,shutil,re; srcs=[n for n in os.listdir('.') if re.match(r'^templates\\\\', n)];\
    (os.makedirs('templates', exist_ok=True), [shutil.move(n, os.path.join('templates', n.split('\\\\')[-1])) for n in srcs]) if srcs else None"

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
