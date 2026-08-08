# fsys Dockerfile — C++20 静态文件托管服务
# 多阶段构建：第一阶段编译 libhttp + libmsg + fsys，第二阶段精简运行时

# ============================================================================
# Stage 1: 构建阶段
# ============================================================================
FROM ubuntu:24.04 AS builder

LABEL maintainer="qingsong"
LABEL service="fsys"
LABEL description="Static File Hosting Service (C++20) - 静态文件托管服务"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libboost-json-dev \
    libssl-dev \
    libcurl4-openssl-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY libhttp/ /build/libhttp/
COPY libmsg/ /build/libmsg/
COPY fsys/ /build/fsys/

# 编译 libmsg
RUN cd /build/libmsg && make clean && make

# 编译 libhttp
RUN cd /build/libhttp && make clean && make \
    CPP="g++" \
    INCS="-I inc" \
    SERVER_LIBS="-lssl -lcrypto -lpthread" \
    CLIENT_LIBS="-lssl -lcrypto -lcurl -lpthread"

# 编译 fsys
RUN cd /build/fsys && make clean && make \
    CPP="g++" \
    INCS="-I ../libhttp/inc -I ../libmsg/inc" \
    LIBS="-L ../libhttp/build -L ../libmsg/build -lus_http -lus_httpclient -lus_msg -lboost_json -lssl -lcrypto -lcurl -lpthread" \
    RPATH="-Wl,-rpath,/app/lib"

# ============================================================================
# Stage 2: 运行时阶段
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    libboost-json1.83.0 \
    libssl3 \
    libcurl4 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

WORKDIR /app

# 复制构建产物
COPY --from=builder /build/fsys/build/fsys /app/fsys
COPY --from=builder /build/libhttp/build/libus_http.so /app/lib/libus_http.so
COPY --from=builder /build/libhttp/build/libus_httpclient.so /app/lib/libus_httpclient.so
COPY --from=builder /build/libmsg/build/libus_msg.so /app/lib/libus_msg.so

# 复制静态文件（public 目录）
COPY static/public/ /app/public/

ENV LD_LIBRARY_PATH=/app/lib

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 9200

# 健康检查：GET /health 期望 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:9200/health -o /dev/null || exit 1

ENV LISTEN_ADDR=0.0.0.0 \
    LISTEN_PORT=9200 \
    STATIC_ROOT=/app/public

CMD ["/app/fsys"]
