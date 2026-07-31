"""
confs — 统一配置服务（pymsg/TCP，默认 127.0.0.1:8010）

集中存放各微服务的配置文件，通过 pymsg 协议分发给内部服务，
替代各服务本地散落的 ini/json/yaml 配置文件，实现配置的统一管理与下发。

pymsg 路由::

    {"_route": "get", "name": "example.ini"}                      → 取配置文件全文
    {"_route": "get", "name": "example.ini", "key": "Server.port"} → 取单个键（ini/json）
    {"_route": "list"}                                             → 列出全部配置
    {"_route": "reload", "name": "example.ini"}                    → 重载指定配置（缺省清空全部缓存）
    {"_route": "health"}                                           → 健康检查

响应格式（与 db / smtp / captcha 一致）::

    {"success": true,  "data": {...}}
    {"success": false, "error": "..."}

配置文件存放于 confs/configs/ 目录（可用 --dir 覆盖），内存缓存 +
文件 mtime 变化自动重载；配置名校验防路径遍历。仅监听 127.0.0.1，不对外暴露。
"""

import configparser
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

# pymsg / log_client 导入：生产环境 pymsg 由 venv（pip install ../pymsg）提供；
# 附加 ../pymsg 源码路径作为无 venv 时的开发兜底
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, ".."))
sys.path.insert(0, os.path.join(_here, "..", "pymsg"))
from pymsg import RpcServer  # noqa: E402
from log_client import logs  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
# 允许分发的配置文件类型（白名单）
ALLOWED_EXT = {".ini", ".conf", ".cfg", ".json", ".yaml", ".yml", ".toml", ".txt"}
# 单配置文件大小上限（1 MiB），防止超大文件读入内存导致 OOM
MAX_CONFIG_SIZE = 1 * 1024 * 1024


# ── 异常 ────────────────────────────────────────────────────────────────

class ConfigError(Exception):
    """配置服务业务错误（由 RpcServer 包装为 success=false 返回）。"""


class ConfigNotFound(ConfigError):
    """配置文件不存在。"""


class ConfigNameInvalid(ConfigError):
    """配置名非法（路径遍历或类型不允许）。"""


class KeyNotFound(ConfigError):
    """配置键不存在或键格式错误。"""


def _iso(ts: float) -> str:
    """时间戳转 ISO 8601（UTC，与 db 服务的日期序列化约定一致）。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# ConfigStore — 配置文件仓库
# ═══════════════════════════════════════════════════════════════════════

class ConfigStore:
    """配置文件仓库：磁盘 configs/ 目录 + 内存缓存，mtime 变化自动重载。

    所有公开方法持锁操作（配置文件小、内部调用频率低，简单持锁即可），
    保证 workers>0 并发回调下的缓存一致性。
    """

    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        os.makedirs(self.root, exist_ok=True)
        self._cache = {}  # name -> {"content": str, "mtime": float, "size": int}
        self._lock = threading.Lock()

    def _resolve(self, name: str) -> str:
        """校验配置名并返回绝对路径（防路径遍历 + 扩展名白名单）。"""
        if not name or name != os.path.basename(name):
            raise ConfigNameInvalid(f"非法配置名: {name!r}")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXT:
            raise ConfigNameInvalid(f"不支持的配置类型: {ext or '(无扩展名)'}")
        path = os.path.realpath(os.path.join(self.root, name))
        if not path.startswith(self.root + os.sep):
            raise ConfigNameInvalid(f"非法配置名: {name!r}")
        if not os.path.isfile(path):
            raise ConfigNotFound(f"配置不存在: {name}")
        return path

    def get(self, name: str) -> dict:
        """取配置内容（带缓存；文件 mtime 变化时自动重新读取）。"""
        path = self._resolve(name)
        with self._lock:
            try:
                st = os.stat(path)
                mtime = st.st_mtime
                size = st.st_size
            except OSError:
                self._cache.pop(name, None)
                raise ConfigNotFound(f"配置不存在或不可读: {name}") from None
            entry = self._cache.get(name)
            if entry is not None and entry["mtime"] == mtime:
                return entry
            if size > MAX_CONFIG_SIZE:
                raise ConfigError(
                    f"配置文件过大: {name}（{size} 字节，上限 {MAX_CONFIG_SIZE} 字节）"
                )
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError) as e:
                raise ConfigError(f"配置读取失败: {name}（{e}）") from None
            entry = {"content": content, "mtime": mtime,
                     "size": len(content.encode("utf-8"))}
            self._cache[name] = entry
            logs.info("confs", f"配置已加载 {name}（{entry['size']}B）")
            return entry

    def list(self) -> list:
        """列出配置目录下全部允许类型的配置文件。"""
        out = []
        for name in sorted(os.listdir(self.root)):
            if os.path.splitext(name)[1].lower() not in ALLOWED_EXT:
                continue
            path = os.path.join(self.root, name)
            if not os.path.isfile(path):
                continue
            st = os.stat(path)
            out.append({"name": name, "size": st.st_size,
                        "mtime": _iso(st.st_mtime),
                        "cached": name in self._cache})
        return out

    def reload(self, name: "str | None" = None) -> list:
        """重载配置：指定 name 立即重新读取；缺省清空全部缓存（下次访问惰性重载）。

        Returns:
            受影响（重载/移出缓存）的配置名列表。
        """
        if name is not None:
            self._resolve(name)  # 校验，文件不存在则抛 ConfigNotFound
            with self._lock:
                self._cache.pop(name, None)
            self.get(name)
            return [name]
        with self._lock:
            dropped = list(self._cache.keys())
            self._cache.clear()
        return dropped

    def cached_count(self) -> int:
        with self._lock:
            return len(self._cache)


# ── 按键提取（可选功能） ────────────────────────────────────────────────

def _extract_key(name: str, content: str, key: str):
    """从配置内容中提取单个键。

    - ini/conf/cfg：键格式 ``Section.option``，返回字符串值；
    - json：键格式为点号路径（如 ``server.port``，列表可用数字下标），
      返回原始 JSON 值；
    - 其他类型不支持按键查询，需取全文后自行解析。
    """
    ext = os.path.splitext(name)[1].lower()

    if ext in (".ini", ".conf", ".cfg"):
        parser = configparser.ConfigParser()
        try:
            parser.read_string(content)
        except configparser.Error as e:
            raise ConfigError(f"INI 解析失败: {name}（{e}）") from None
        section, _, option = key.partition(".")
        if not section or not option:
            raise KeyNotFound(f"INI 键格式应为 Section.option: {key!r}")
        if parser.has_option(section, option):
            return parser.get(section, option)
        raise KeyNotFound(f"键不存在: {name} -> {key}")

    if ext == ".json":
        try:
            node = json.loads(content)
        except json.JSONDecodeError as e:
            raise ConfigError(f"JSON 解析失败: {name}（{e}）") from None
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise KeyNotFound(f"键不存在: {name} -> {key}")
        return node

    raise ConfigError(f"{ext} 类型不支持按键查询，请取全文后自行解析")


# ═══════════════════════════════════════════════════════════════════════
# ConfsServer — 主服务类
# ═══════════════════════════════════════════════════════════════════════

class ConfsServer:
    """统一配置服务：集中管理并向内部微服务分发配置文件。"""

    def __init__(self, addr="127.0.0.1", port=8010, workers=4, config_dir=CONFIG_DIR):
        self.addr = addr
        self.port = port
        self._started_at = time.time()
        self.store = ConfigStore(config_dir)
        self._server = RpcServer(
            addr, port, workers=workers,
            on_error=lambda route, e: logs.error(
                "confs", f"路由 {route} 处理异常: {e}"),
        )
        self._server.add_route("get", self._handle_get)
        self._server.add_route("list", self._handle_list)
        self._server.add_route("reload", self._handle_reload)
        self._server.add_route("health", self._handle_health)

    # ── 启动 / 停止 ──────────────────────────────────────────────────

    def start(self) -> None:
        """启动服务（阻塞当前线程）。"""
        print(f"[confs] 监听 tcp://{self.addr}:{self.port}，配置目录 {self.store.root}")
        logs.info("confs", f"开始监听 tcp://{self.addr}:{self.port}"
                           f"（配置目录 {self.store.root}）")
        self._server.start()

    def stop(self) -> None:
        """从另一线程停止服务。"""
        self._server.stop()

    # ── 路由处理 ─────────────────────────────────────────────────────

    def _handle_get(self, req):
        """取配置文件全文；带 key 时返回单个键的值（仅 ini/json）。"""
        name = str(req.get("name", "")).strip()
        if not name:
            raise ConfigError("缺少 name 字段")
        entry = self.store.get(name)

        key = req.get("key")
        if key is None:
            logs.info("confs", f"分发配置 {name}（{entry['size']}B）")
            return {"name": name, "content": entry["content"],
                    "size": entry["size"], "mtime": _iso(entry["mtime"])}

        key = str(key).strip()
        value = _extract_key(name, entry["content"], key)
        logs.info("confs", f"分发配置键 {name} -> {key}")
        return {"name": name, "key": key, "value": value}

    def _handle_list(self, _req):
        """列出配置目录下全部配置文件及其元信息。"""
        return {"config_dir": self.store.root, "configs": self.store.list()}

    def _handle_reload(self, req):
        """重载指定配置；缺省 name 时清空全部缓存（下次访问惰性重载）。"""
        name = req.get("name")
        names = self.store.reload(str(name).strip() if name else None)
        if name:
            logs.info("confs", f"重载配置 {names[0]}")
        else:
            logs.info("confs", f"清空配置缓存（{len(names)} 项）")
        return {"reloaded": names}

    def _handle_health(self, _req):
        return {"status": "ok", "service": "confs",
                "config_dir": self.store.root,
                "cached": self.store.cached_count(),
                "uptime": int(time.time() - self._started_at)}


# ═══════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="confs — 统一配置服务（pymsg/TCP）")
    parser.add_argument("--addr", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8010, help="监听端口")
    parser.add_argument("--workers", type=int, default=4,
                        help="回调并发线程数（0=同步串行模式）")
    parser.add_argument("--dir", default=CONFIG_DIR, help="配置文件目录")
    args = parser.parse_args()

    server = ConfsServer(args.addr, args.port,
                         workers=args.workers, config_dir=args.dir)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
