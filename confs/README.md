# confs 统一配置服务（pymsg/TCP）

集中存放各微服务的配置文件，通过 pymsg 协议分发给内部服务，替代各服务本地散落的 ini/json/yaml 配置，实现配置的统一管理与下发。

服务由 `core`（PMC）的 `init.txt` 拉起，监听 `127.0.0.1:8010`（TCP），仅允许本机调用。

## 功能特性

- **集中托管**：各微服务的配置文件统一存放于 `configs/` 目录，按配置名分发
- **多格式支持**：`.ini` / `.conf` / `.cfg` / `.json` / `.yaml` / `.yml` / `.toml` / `.txt`
- **内存缓存 + 自动重载**：文件 mtime 变化时自动重新读取，无需重启服务
- **按键取值**：ini/conf/cfg 支持 `Section.option` 取单键，json 支持点号路径（含数字下标）
- **路径遍历防护**：basename 校验 + realpath 前缀校验 + 扩展名白名单
- **线程安全**：所有公开方法持锁，保证 `workers>0` 并发回调下的缓存一致性
- **仅监听本机**：默认 `127.0.0.1:8010`，不对外暴露

## 技术栈

- Python 3
- [pymsg](../pymsg/) —— TCP 长度前缀帧协议（`[4字节大端长度][UTF-8 JSON]`）

## 安装

```bash
./_set.sh     # 建 venv 并安装 requirements.txt（本地 ../pymsg + nuitka）
```

## 运行

```bash
# 方式一：通过启动脚本（推荐，由 core 守护时即使用此方式）
cd confs && ./_run.sh

# 方式二：直接运行
python3 confs.py                          # 默认 127.0.0.1:8010，配置目录 configs/
python3 confs.py --addr 127.0.0.1 --port 8010 --workers 4 --dir configs/
```

服务监听 `tcp://127.0.0.1:8010`，配置目录默认为同级的 `configs/`。生产环境由 core（Nidus）按 init.txt 统一拉起，守护靠 systemd。

## 协议与路由

服务基于 pymsg 协议：每条消息为完整 JSON，`_route` 字段指定路由。响应格式与 db / smtp / captcha 一致：

```json
{"success": true,  "data": {...}}
{"success": false, "error": "..."}
```

| _route   | 说明                                       |
| -------- | ------------------------------------------ |
| `get`    | 取配置文件全文；带 `key` 时返回单键值（仅 ini/json） |
| `list`   | 列出配置目录下全部配置文件及其元信息          |
| `reload` | 重载指定配置；缺省 `name` 时清空全部缓存      |
| `health` | 健康检查                                   |

### 1. get — 取配置

**请求**（取全文）：

```json
{"_route": "get", "name": "example.ini"}
```

**响应**：

```json
{
  "success": true,
  "data": {
    "name": "example.ini",
    "content": "[Server]\naddress = 127.0.0.1\nport = 8010\n",
    "size": 45,
    "mtime": "2026-08-01T10:00:00+00:00"
  }
}
```

**请求**（按键取值，仅 ini/conf/cfg 与 json 支持）：

```json
{"_route": "get", "name": "example.ini", "key": "Server.port"}
```

**响应**：

```json
{"success": true, "data": {"name": "example.ini", "key": "Server.port", "value": "8010"}}
```

> - ini/conf/cfg：键格式为 `Section.option`，返回字符串值
> - json：键格式为点号路径（如 `server.port`），列表可用数字下标（如 `servers.0.host`），返回原始 JSON 值
> - 其他类型（yaml/yml/toml/txt）不支持按键查询，需取全文后自行解析

### 2. list — 列出配置

**请求**：`{"_route": "list"}`

**响应**：

```json
{
  "success": true,
  "data": {
    "config_dir": "/opt/micro_servers/confs/configs",
    "configs": [
      {"name": "example.ini", "size": 45, "mtime": "2026-08-01T10:00:00+00:00", "cached": true}
    ]
  }
}
```

### 3. reload — 重载配置

**请求**（重载指定配置）：

```json
{"_route": "reload", "name": "example.ini"}
```

**请求**（清空全部缓存，下次访问惰性重载）：

```json
{"_route": "reload"}
```

**响应**：

```json
{"success": true, "data": {"reloaded": ["example.ini"]}}
```

> 指定 `name` 时立即重新读取并返回受影响列表；缺省时仅清空缓存（返回被清除的配置名列表），实际重载发生在下次访问。

### 4. health — 健康检查

**请求**：`{"_route": "health"}`

**响应**：

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "service": "confs",
    "config_dir": "/opt/micro_servers/confs/configs",
    "cached": 1,
    "uptime": 3600
  }
}
```

### 调用示例（Python）

```python
import json
from pymsg import MsgClient

c = MsgClient("127.0.0.1", 8010, "my-service")

# 取配置全文
resp = json.loads(c.call(json.dumps({"_route": "get", "name": "example.ini"}), timeout=5))
content = resp["data"]["content"]

# 按键取值
resp = json.loads(c.call(json.dumps({
    "_route": "get", "name": "example.ini", "key": "Server.port"
}), timeout=5))
port = resp["data"]["value"]
```

## 缓存机制

- **内存缓存**：每个配置首次读取后缓存于 `ConfigStore._cache`，结构为 `{name: {content, mtime, size}}`
- **自动重载**：每次 `get` 时比对文件 mtime，变化则重新读取并更新缓存
- **手动重载**：`reload` 路由可指定单个配置立即重读，或清空全部缓存（下次访问惰性重载）
- **线程安全**：所有缓存操作持 `threading.Lock`，并发安全

## 安全机制

- **扩展名白名单**：仅允许 `.ini/.conf/.cfg/.json/.yaml/.yml/.toml/.txt`，其余一律拒绝
- **路径遍历防护**：配置名必须等于 `os.path.basename(name)`，且 realpath 必须位于 `configs/` 目录前缀之下
- **仅监听本机**：默认 `127.0.0.1`，不对外暴露，仅供内部服务调用
- **无鉴权**：当前无身份校验，依赖"仅本机 + 内部网络"的信任边界；如需承载敏感配置（如 API 密钥），应额外评估鉴权方案

## 集成关系

- 由 `core`（`PMC`）通过 `init.txt` 中的 `bash /opt/micro_servers/confs/_run.sh` 拉起并守护（在 db 之后、ai_review 之前启动）
- 各内部服务通过 pymsg 调 `get` 路由按需获取配置，替代本地散落的 ini/json/yaml
- 自身日志通过 `log_client.py` 异步推送至 `db:8005` 的 Logs 表（与其他服务一致）

## 项目结构

```
confs/
├── confs.py             # 服务主程序（ConfigStore + ConfsServer）
├── log_client.py        # 统一日志客户端 SDK（与其他服务一致的副本）
├── configs/             # 配置文件存放目录（被分发对象）
│   ├── example.ini      # 示例配置
│   └── static.ini       # static 服务配置（已接入分发）
├── _set.sh              # 初始化脚本（建 venv + 安装依赖）
├── _run.sh              # 启动脚本（venv 中 exec python3 confs.py）
├── requirements.txt     # Python 依赖（本地 ../pymsg + nuitka）
└── README.md
```

## 注意事项

- 配置文件需手动放入 `configs/` 目录（或用 `--dir` 指定其他目录），服务不会自动生成业务配置
- 文件以 UTF-8 读取，非 UTF-8 文件会返回 `配置读取失败` 错误
- 配置名区分大小写（与文件系统一致）
- 缓存基于文件 mtime 判断变化，若同秒内修改且 mtime 精度不足（如某些 FAT 文件系统），可能无法及时重载——可手动调 `reload` 路由强制刷新
- `static` 已接入 confs 统一分发（启动时拉取 `static.ini`，本地 `config.ini` 为 fail-safe 回退）；其余服务仍读本地配置文件，接入示例见上文"调用示例"
