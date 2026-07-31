FROM python:3.11-slim

LABEL maintainer="qingsong"
LABEL service="dnfs"
LABEL description="Dendrachy Network File-System - 树状网络文件系统"

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖（如果有需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 先复制pymsg共享库并安装
COPY pymsg/ /app/pymsg/
RUN pip install /app/pymsg/

# 复制requirements并安装依赖
COPY dnfs/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 移除flask_sqlalchemy遗留依赖（已不使用）
RUN pip uninstall -y flask_sqlalchemy 2>/dev/null || true

# 复制应用代码
COPY dnfs/ /app/dnfs/

# 创建非root用户运行（安全最佳实践）
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/dnfs

# 暴露端口
EXPOSE 8006

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8006/get_notes', data=b'{\"pid\":0}', timeout=3)" || exit 1

# 启动服务
CMD ["python", "dnfs.py"]
