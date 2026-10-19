# syntax=docker/dockerfile:1
# Build context: repository root (see docker-compose.yml)
FROM python:3.12-slim
ARG HTTP_PROXY HTTPS_PROXY NO_PROXY
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY libs/zuvo_common /tmp/zuvo_common
COPY zuvo-ai-rag/requirements.txt /tmp/requirements.txt
RUN pip install /tmp/zuvo_common -r /tmp/requirements.txt && rm -rf /tmp/zuvo_common /tmp/requirements.txt
COPY zuvo-ai-rag/main.py zuvo-ai-rag/config.yaml ./
COPY zuvo-ai-rag/api ./api
COPY zuvo-ai-rag/components ./components
COPY zuvo-ai-rag/services ./services
COPY zuvo-ai-rag/utils ./utils
COPY zuvo-ai-rag/scripts ./scripts
COPY zuvo-ai-rag/eval ./eval
COPY config ./config
COPY tenants ./tenants
RUN useradd --system --uid 10001 zuvo && chown -R zuvo /app
USER zuvo
EXPOSE 8020
CMD ["python", "main.py"]
