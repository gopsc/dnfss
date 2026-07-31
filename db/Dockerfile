FROM python:3.11-slim

LABEL maintainer="qingsong"
LABEL service="db"
LABEL description="dbv2 - pymsg协议数据库服务"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 安装pymsg共享库
COPY pymsg/ /app/pymsg/
RUN pip install /app/pymsg/

# 安装依赖
COPY db/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY db/ /app/db/

# 入口脚本
RUN chmod +x /app/db/docker-entrypoint.sh

# 创建数据持久化目录并设置权限
RUN mkdir -p /app/data && chown -R 1000:1000 /app/data
VOLUME ["/app/data"]

# 创建非root用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/db

EXPOSE 8005

# 健康检查：尝试连接TCP端口
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost', 8005)); s.close()" || exit 1

# 默认使用SQLite，数据持久化到/app/data/database.db
ENV DATABASE_URL=sqlite:////app/data/database.db \
    LISTEN_ADDR=0.0.0.0 \
    LISTEN_PORT=8005 \
    WORKERS=4

ENTRYPOINT ["/app/db/docker-entrypoint.sh"]
