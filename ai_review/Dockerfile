FROM python:3.11-slim

LABEL maintainer="qingsong"
LABEL service="ai_review"
LABEL description="AI内容审核服务 - DeepSeek + RAG"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 安装依赖（ai_review不依赖pymsg）
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY . /app/

# 创建ChromaDB持久化目录
RUN mkdir -p /app/chroma
VOLUME ["/app/chroma"]

# 创建非root用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8007

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8007/health', timeout=5)" || exit 1

# 默认环境变量
ENV AI_REVIEW_HOST=0.0.0.0 \
    AI_REVIEW_PORT=8007 \
    CHROMA_PERSIST_DIR=/app/chroma

CMD ["python", "ai_review.py"]
