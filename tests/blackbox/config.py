"""
micro_servers 黑盒测试 — 服务地址与端口配置。

每个服务定义: name, port, description, host。
test_runner.py 通过 --url 参数自动识别目标服务。
"""

SERVICES = {
    "gateway": {
        "name": "gateway",
        "host": "127.0.0.1",
        "port": 8443,
        "description": "应用网关 (认证+反向代理)",
        "test_file": "test_gateway.py",
    },
    "static": {
        "name": "static",
        "host": "127.0.0.1",
        "port": 9200,
        "description": "前端静态服务 (文件托管+视频流)",
        "test_file": "test_static.py",
    },
    "dnfs": {
        "name": "dnfs",
        "host": "127.0.0.1",
        "port": 8006,
        "description": "树状笔记服务 (CRUD+内容审核)",
        "test_file": "test_dnfs.py",
    },
    "db": {
        "name": "db",
        "host": "127.0.0.1",
        "port": 8005,
        "description": "通用数据库服务 (CRUD+集中日志)",
        "test_file": "test_db.py",
    },
    "ai_review": {
        "name": "ai_review",
        "host": "127.0.0.1",
        "port": 8007,
        "description": "AI 内容审核服务 (DeepSeek+ChromaDB RAG)",
        "test_file": "test_ai_review.py",
    },
    "captcha": {
        "name": "captcha",
        "host": "127.0.0.1",
        "port": 8009,
        "description": "验证码服务 (图片生成+校验)",
        "test_file": "test_captcha.py",
    },
    "smtp": {
        "name": "smtp",
        "host": "127.0.0.1",
        "port": 8008,
        "description": "邮件验证服务 (发送+校验)",
        "test_file": "test_smtp.py",
    },
    "waf": {
        "name": "waf",
        "host": "127.0.0.1",
        "port": 443,
        "description": "WAF 安全网关 (TLS+Coraza规则引擎)",
        "test_file": "test_waf.py",
    },
}

# 端口到服务的反向映射
PORT_MAP = {v["port"]: v["name"] for v in SERVICES.values()}


def detect_service(url: str):
    """从 URL 中识别目标服务。返回服务名或 None。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    port = parsed.port
    if port is None:
        # 默认端口猜测
        if parsed.scheme == "https":
            port = 443
        else:
            port = 80
    return PORT_MAP.get(port)


def get_url(name: str) -> str:
    """获取服务的完整 HTTP URL。"""
    svc = SERVICES.get(name)
    if not svc:
        raise ValueError(f"未知服务: {name}")
    return f"http://{svc['host']}:{svc['port']}"
