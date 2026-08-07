# dnfs2 Dockerfile — C++20 树状网络文件系统
# 多阶段构建：第一阶段编译 libhttp + libmsg + dnfs2，第二阶段精简运行时
#
# 注：Debian bookworm 的 boost 版本为 1.74，不含 boost/json.hpp（需 1.75+），
# 因此选用 Ubuntu 24.04（boost 1.83）作为基础镜像。

# ============================================================================
# Stage 1: 构建阶段
# ============================================================================
FROM ubuntu:24.04 AS builder

LABEL maintainer="qingsong"
LABEL service="dnfs2"
LABEL description="Dendrachy Network File-System (C++20) - 树状网络文件系统"

ENV DEBIAN_FRONTEND=noninteractive

# 安装构建依赖：g++（C++20）、boost-json、OpenSSL、libcurl、make
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libboost-json-dev \
    libssl-dev \
    libcurl4-openssl-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 复制依赖库源码（.dockerignore 中 lib*/ 已为 libhttp/libmsg 豁免）
COPY libhttp/ /build/libhttp/
COPY libmsg/ /build/libmsg/
COPY dnfs2/ /build/dnfs2/

# 编译 libmsg（无外部链接依赖）
RUN cd /build/libmsg && make clean && make

# 编译 libhttp（覆盖 Makefile 中 /opt/homebrew 路径，使用系统路径）
RUN cd /build/libhttp && make clean && make \
    CPP="g++" \
    INCS="-I inc" \
    SERVER_LIBS="-lssl -lcrypto -lpthread" \
    CLIENT_LIBS="-lssl -lcrypto -lcurl -lpthread"

# 编译 dnfs2（链接本地构建的 libhttp/libmsg + 系统 boost/openssl/curl）
RUN cd /build/dnfs2 && make clean && make \
    CPP="g++" \
    INCS="-I ../libhttp/inc -I ../libmsg/inc" \
    LIBS="-L ../libhttp/build -L ../libmsg/build -lus_http -lus_httpclient -lus_msg -lboost_json -lssl -lcrypto -lcurl -lpthread" \
    RPATH="-Wl,-rpath,/app/lib"

# ============================================================================
# Stage 2: 运行时阶段
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# 安装运行时依赖（仅动态库，无开发头文件）+ curl（健康检查）
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
COPY --from=builder /build/dnfs2/build/dnfs2 /app/dnfs2
COPY --from=builder /build/libhttp/build/libus_http.so /app/lib/libus_http.so
COPY --from=builder /build/libhttp/build/libus_httpclient.so /app/lib/libus_httpclient.so
COPY --from=builder /build/libmsg/build/libus_msg.so /app/lib/libus_msg.so

# 设置动态库搜索路径
ENV LD_LIBRARY_PATH=/app/lib

# 设置权限
RUN chown -R appuser:appuser /app
USER appuser

# 暴露端口（与 dnfs 一致）
EXPOSE 8006

# 健康检查：POST /get_notes {"pid":0} 期望 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf -X POST http://localhost:8006/get_notes \
        -H "Content-Type: application/json" \
        -d '{"pid":0}' -o /dev/null || exit 1

# 默认环境变量（与 docker-compose.yml 中 dnfs 服务对齐）
ENV LISTEN_ADDR=0.0.0.0 \
    LISTEN_PORT=8006

# 启动服务
CMD ["/app/dnfs2"]
