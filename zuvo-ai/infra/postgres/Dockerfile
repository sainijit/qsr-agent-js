# Postgres 16 (alpine, same base as before so existing volumes keep their collation) + pgvector.
FROM postgres:16-alpine
ARG PGVECTOR_VERSION=v0.8.0
ARG HTTP_PROXY HTTPS_PROXY
RUN apk add --no-cache --virtual .build-deps git build-base \
 && git clone --depth 1 --branch ${PGVECTOR_VERSION} https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && cd /tmp/pgvector && make with_llvm=no OPTFLAGS="" && make with_llvm=no install \
 && cd / && rm -rf /tmp/pgvector && apk del .build-deps
