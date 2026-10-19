# syntax=docker/dockerfile:1
# Build context: repository root (see docker-compose.yml)
FROM python:3.12-slim
ARG HTTP_PROXY HTTPS_PROXY NO_PROXY
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY libs/zuvo_common /tmp/zuvo_common
COPY services/persister/requirements.txt /tmp/requirements.txt
RUN pip install /tmp/zuvo_common -r /tmp/requirements.txt && rm -rf /tmp/zuvo_common /tmp/requirements.txt
COPY services/persister/persister ./persister
COPY config ./config
COPY tenants ./tenants
RUN useradd --system --uid 10001 zuvo && chown -R zuvo /app
USER zuvo
CMD ["python", "-m", "persister.main"]
