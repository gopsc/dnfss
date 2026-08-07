# db2 Dockerfile — C++20 数据库 RPC 服务（libmsg + SQLite）
# 多阶段构建：第一阶段编译 libmsg + db2，第二阶段精简运行时
#
# 注：Debian bookworm 的 boost 版本为 1.74，不含 boost/json.hpp（需 1.75+），
# 因此选用 Ubuntu 24.04（boost 1.83）作为基础镜像。

# ============================================================================
# Stage 1: 构建阶段
# ============================================================================
FROM ubuntu:24.04 AS builder

LABEL maintainer="qingsong"
LABEL service="db2"
LABEL description="数据库 RPC 服务 (C++20) - libmsg + SQLite"

ENV DEBIAN_FRONTEND=noninteractive

# 安装构建依赖：g++（C++20）、boost-json、SQLite3
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libboost-json-dev \
    libsqlite3-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 复制依赖库源码（.dockerignore 中 lib*/ 已为 libmsg 豁免）
COPY libmsg/ /build/libmsg/
COPY db2/ /build/db2/

# 编译 libmsg（无外部链接依赖）
RUN cd /build/libmsg && make clean && make

# 编译 db2（覆盖 Makefile 中的 RPATH，使用容器内固定路径）
RUN cd /build/db2 && make clean && make \
    CPP="g++" \
    INCS="-I ../libmsg/inc" \
    LIBS="-L ../libmsg/build -lus_msg -lboost_json -lsqlite3" \
    RPATH="-Wl,-rpath,/app/lib"

# ============================================================================
# Stage 2: 运行时阶段
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# 安装运行时依赖（仅动态库）+ sqlite3 CLI（entrypoint 初始化 schema）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libboost-json1.83.0 \
    libsqlite3-0 \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

WORKDIR /app

# 复制构建产物
COPY --from=builder /build/db2/build/db2 /app/db2
COPY --from=builder /build/libmsg/build/libus_msg.so /app/lib/libus_msg.so

# 复制 entrypoint 脚本
COPY db2/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 设置动态库搜索路径
ENV LD_LIBRARY_PATH=/app/lib

# 创建数据持久化目录并设置权限
RUN mkdir -p /app/data && chown -R appuser:appuser /app
VOLUME ["/app/data"]

USER appuser

# 暴露端口（与 db 一致）
EXPOSE 8005

# 健康检查：TCP 端口探测（db2 是 pymsg TCP 协议，非 HTTP）
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD bash -c 'echo > /dev/tcp/localhost/8005' || exit 1

# 默认环境变量（与 docker-compose.yml 中 db 服务对齐）
ENV LISTEN_ADDR=0.0.0.0 \
    LISTEN_PORT=8005 \
    WORKERS=4 \
    DATABASE_URL=sqlite:////app/data/database.db

ENTRYPOINT ["/app/docker-entrypoint.sh"]
