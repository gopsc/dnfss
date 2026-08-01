# confs 统一配置服务（pymsg RpcServer/TCP）

集中存放各微服务的配置文件，通过 pymsg RPC 协议分发给内部服务，替代各服务本地散落的 ini/json/yaml 配置，实现配置的统一管理与下发。

服务由 `core`（PMC）的 `init.txt` 拉起，监听 `127.0.0.1:8010`（TCP），仅允许本机调用。

## 功能特性

- **集中托管**：各微服务的配置文件统一存放于 `configs/` 目录，按配置名分发
- **多格式支持**：`.ini` / `.conf` / `.cfg` / `.json` / `.yaml` / `.yml` / `.toml` / `.txt`
- **内存缓存 + 自动重载**：文件 mtime 变化时自动重新读取，无需重启服务
- **按键取值**：ini/conf/cfg 支持 `Section.option` 取单键，json 支持点号路径（含数字下标）
- **路径遍历防护**：basename 校验 + realpath 前缀校验 + 扩展名白名单
- **TOCTOU 防护**：所有文件系统操作（realpath → stat → open）在锁内原子完成，防止符号链接替换竞态
- **缓存防污染**：`get()` 返回缓存条目的浅拷贝，调用方修改返回值不会影响内部缓存
- **list 符号链接防护**：列出配置时同样解析 realpath 校验前缀，逃逸符号链接不会泄露外部文件元信息
- **严格参数校验**：`name`/`key` 参数必须为字符串，空字符串/空白字符串/非字符串类型明确返回错误，不会产生误行为
- **INI 安全解析**：禁用 ConfigParser 的 `%(key)s` 插值功能，配置值中包含裸 `%` 字符不会触发解析错误
- **文件大小限制**：单配置文件上限 1 MiB，防止超大文件导致 OOM
- **线程安全**：所有公开方法持锁，保证 `workers>0` 并发回调下的缓存一致性
- **仅监听本机**：默认 `127.0.0.1:8010`，不对外暴露
- **Slowloris 防护**：pymsg MsgServer 默认设置 10 秒接收超时，慢速半开连接自动断开

## 技术栈

- Python 3
- [pymsg](../pymsg/) —— TCP 长度前缀帧协议（`[4字节大端长度][UTF-8 JSON]`），上层提供 `RpcServer`/`RpcClient` 高层 JSON RPC 封装

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

服务基于 pymsg RPC 协议：客户端使用 `RpcClient.call(route, payload)` 传 dict，无需手动拼 `_route` 或手动 JSON 序列化/反序列化。响应格式与 db / smtp / captcha 一致：

```json
{"success": true,  "data": {...}}
{"success": false, "error": "..."}
```

`RpcClient.call()` 成功时直接返回 `data` 字段的值（dict/str/None 等），失败时抛出 `RpcError`。

| 路由   | 说明                                       |
| ------ | ------------------------------------------ |
| `get`    | 取配置文件全文；带 `key` 时返回单键值（仅 ini/json） |
| `list`   | 列出配置目录下全部配置文件及其元信息          |
| `reload` | 重载指定配置；省略 `name`（或传 `null`）时清空全部缓存 |
| `health` | 健康检查                                   |

### 1. get — 取配置

**请求**（取全文）：

```python
c.call("get", {"name": "example.ini"})
```

**返回**（data 字段）：

```json
{
  "name": "example.ini",
  "content": "[server]\nhost = 127.0.0.1\nport = 8010\nratio = 80%\n",
  "size": 60,
  "mtime": "2026-08-01T10:00:00+00:00"
}
```

**请求**（按键取值，仅 ini/conf/cfg 与 json 支持）：

```python
c.call("get", {"name": "example.ini", "key": "server.port"})
```

**返回**：

```json
{"name": "example.ini", "key": "server.port", "value": "8010"}
```

参数校验：
- `name` 必填，必须为非空字符串；非字符串/空字符串返回 `ConfigError`
- `key` 可选；若提供必须为非空字符串，非字符串/空字符串返回 `ConfigError`

> - ini/conf/cfg：键格式为 `Section.option`，返回字符串值；插值已禁用，`%` 字符原样返回
> - json：键格式为点号路径（如 `server.port`），列表可用数字下标（如 `servers.0.host`），返回原始 JSON 值
> - 其他类型（yaml/yml/toml/txt）不支持按键查询，需取全文后自行解析

### 2. list — 列出配置

**请求**：

```python
c.call("list", {})
```

**返回**：

```json
{
  "config_dir": "/opt/micro_servers/confs/configs",
  "configs": [
    {"name": "example.ini", "size": 60, "mtime": "2026-08-01T10:00:00+00:00", "cached": true}
  ]
}
```

> 逃逸到 `configs/` 目录外的符号链接不会出现在列表中。

### 3. reload — 重载配置

**重载指定配置**（立即重新读取文件）：

```python
c.call("reload", {"name": "example.ini"})
```

**清空全部缓存**（下次访问惰性重载）：

```python
c.call("reload", {})        # 省略 name 字段
# 或
c.call("reload", {"name": None})
```

**返回**：

```json
{"reloaded": ["example.ini"]}
```

参数校验：
- 省略 `name` 或 `name` 为 `null`：清空全部缓存，返回被清除的配置名列表
- `name` 为非字符串（数字、布尔、列表等）：返回 `ConfigError("name 字段必须是字符串")`
- `name` 为空字符串或纯空白：返回 `ConfigError("name 字段不能为空（要清空全部缓存请省略 name 字段）")`
- 指定合法 `name`：立即重新读取该配置并返回受影响列表

### 4. health — 健康检查

**请求**：

```python
c.call("health", {})
```

**返回**：

```json
{
  "status": "ok",
  "service": "confs",
  "config_dir": "/opt/micro_servers/confs/configs",
  "cached": 1,
  "uptime": 3600
}
```

### 调用示例（Python）

```python
from pymsg import RpcClient, RpcError

c = RpcClient("127.0.0.1", 8010, "my-service")

try:
    # 取配置全文（c.call() 直接返回 data 字段，无需 resp["data"]）
    entry = c.call("get", {"name": "static.ini"}, timeout=5)
    content = entry["content"]
    print(f"static.ini size={entry['size']}")

    # 按键取值
    port = c.call("get", {"name": "static.ini", "key": "server.port"}, timeout=5)
    print(f"static server.port = {port}")

    # 列出所有配置
    lst = c.call("list", {}, timeout=5)
    for cfg in lst["configs"]:
        print(cfg["name"], cfg["size"])

    # 重载指定配置
    c.call("reload", {"name": "static.ini"})

    # 清空全部缓存
    c.call("reload", {})

except RpcError as e:
    print(f"confs 调用失败: {e}")
```

## 缓存机制

- **内存缓存**：每个配置首次读取后缓存于 `ConfigStore._cache`，结构为 `{name: {content, mtime, size}}`
- **自动重载**：每次 `get` 时在锁内比对文件 mtime（通过 `os.stat()`），变化则重新读取并更新缓存
- **手动重载**：`reload` 路由可指定单个配置立即重读，或清空全部缓存（下次访问惰性重载）
- **返回值隔离**：`get()` 返回缓存条目的**浅拷贝**，调用方对返回 dict 的修改不会污染内部缓存
- **线程安全**：所有缓存操作持 `threading.Lock`，并发安全

## 安全机制

- **扩展名白名单**：仅允许 `.ini/.conf/.cfg/.json/.yaml/.yml/.toml/.txt`，其余一律拒绝
- **路径遍历防护**：配置名必须等于 `os.path.basename(name)`，且 realpath 必须位于 `configs/` 目录前缀之下；符号链接会被 `os.path.realpath()` 解析后校验
- **TOCTOU 防护**：所有文件系统操作（`realpath` → `stat` → `open`）均在持锁状态下原子完成，不存在"先校验后打开"的时间窗口，防止符号链接替换攻击
- **文件大小限制**：单配置文件上限 1 MiB（`MAX_CONFIG_SIZE`），超过则拒绝读取并返回错误，防止超大文件导致内存溢出
- **Slowloris 防护**：pymsg `MsgServer` 默认设置 10 秒接收超时，慢速发送的半开连接会被自动断开
- **缓存防污染**：`get()` 返回浅拷贝，调用方无法通过修改返回值污染内部缓存
- **list 符号链接防护**：列出配置时对每个文件做 realpath 前缀校验，逃逸到目录外的符号链接被跳过，不泄露外部文件元信息（大小/mtime）
- **严格参数校验**：`name`/`key` 参数必须为非空字符串，类型错误或空值明确返回 `ConfigError`，不产生误行为（如空字符串误清空全部缓存）
- **INI 安全解析**：使用 `ConfigParser(interpolation=None)` 禁用 `%(key)s` 插值，配置值中裸 `%` 字符不会触发 `InterpolationError`
- **仅监听本机**：默认 `127.0.0.1`，不对外暴露，仅供内部服务调用
- **无鉴权**：当前无身份校验，依赖"仅本机 + 内部网络"的信任边界；如需承载敏感配置（如 API 密钥），应额外评估鉴权方案

## 集成关系

- 由 `core`（`PMC`）通过 `init.txt` 中的 `bash /opt/micro_servers/confs/_run.sh` 拉起并守护（confs 最先启动，所有依赖 confs 的服务必须在 confs 就绪后启动；confs 日志推送 db 为异步 fail-safe，db 不可用时静默丢弃，不阻塞 confs 启动）
- 各内部服务通过 pymsg `RpcClient` 调 `get` 路由按需获取配置，替代本地散落的 ini/json/yaml
- 自身日志通过 `log_client.py` 异步推送至 `db:8005` 的 Logs 表（与其他服务一致）

### 已接入的服务

| 服务 | 配置文件 | 接入方式 |
|------|---------|---------|
| [static](../static/) | [static.ini](configs/static.ini) | confs 强依赖（confs 不可用时 static 直接退出）→ 环境变量覆盖（最高优先级，Docker 兼容） |
| [dnfs](../dnfs/) | [dnfs.ini](configs/dnfs.ini) | confs 强依赖（confs 不可用时 dnfs 直接退出）→ 环境变量覆盖（最高优先级，Docker 兼容） |
| [db](../db/) | [db.ini](configs/db.ini) | confs 强依赖（confs 不可用时 db 直接退出）→ 环境变量覆盖（最高优先级，Docker 兼容） |

static 的接入实现见 [static/fsys.py](../static/fsys.py)：
- 两层加载顺序：confs（static.ini，强依赖）→ 环境变量覆盖（优先级最高）
- confs 连接失败或 static.ini 缺失必需配置项时，static 直接 `sys.exit(1)` 终止，不使用默认值或回退本地文件
- 配置项涵盖：`[server]` host/port、`[directory]` root、`[ssl]` enabled/cert_file/key_file（ssl 有默认值）、`[cors]` allowed_origins
- confs 连接地址为引导配置：环境变量 `CONFS_HOST`/`CONFS_PORT` 优先，默认 `127.0.0.1:8010`
- 支持的环境变量：`LISTEN_ADDR`/`LISTEN_PORT`/`STATIC_ROOT`/`CONFS_HOST`/`CONFS_PORT`
- 启动顺序：confs 必须先于 static 启动；已删除 `--config` 命令行参数和本地 `config.ini` 回退逻辑

dnfs 的接入实现见 [dnfs/dnfs.py](../dnfs/dnfs.py)：
- 两层加载顺序：confs（dnfs.ini，强依赖）→ 环境变量覆盖（优先级最高）
- confs 连接失败或 dnfs.ini 缺失必需配置项时，dnfs 直接 `sys.exit(1)` 终止，不使用默认值
- 配置项涵盖：`[server]` host/port、`[database]` host/port/timeout、`[ai_review]` url/timeout
- confs 连接地址为引导配置：环境变量 `CONFS_HOST`/`CONFS_PORT` 优先，默认 `127.0.0.1:8010`
- 支持的环境变量：`LISTEN_ADDR`/`LISTEN_PORT`/`DB_HOST`/`DB_PORT`/`REVIEW_URL`/`CONFS_HOST`/`CONFS_PORT`
- 启动顺序：confs → db → dnfs → ai_review/...（dnfs 同时依赖 confs 和 db）

db 的接入实现见 [db/dbv2.py](../db/dbv2.py)：
- 两层加载顺序：confs（db.ini，强依赖）→ 环境变量覆盖（优先级最高）
- confs 连接失败或 db.ini 缺失必需配置项时，db 直接 `sys.exit(1)` 终止，不使用默认值
- 配置项涵盖：`[server]` host/port/workers、`[database]` url、`[logs]` retention_days/max_records/cleanup_interval
- confs 连接地址为引导配置：环境变量 `CONFS_HOST`/`CONFS_PORT` 优先，默认 `127.0.0.1:8010`
- 支持的环境变量：`LISTEN_ADDR`/`LISTEN_PORT`/`WORKERS`/`DATABASE_URL`/`CONFS_HOST`/`CONFS_PORT`
- 启动顺序：confs 必须先于 db 启动（confs → db → ai_review/...）
- 独立运维工具 `db_tools.py`（backup/export/import 等）仍直接读 `DATABASE_URL` 环境变量，不依赖 confs，避免运维脚本依赖服务就绪

其他服务（gateway / waf / ai_review / smtp）尚未接入，仍读各自本地配置。

## 项目结构

```
confs/
├── confs.py             # 服务主程序（ConfigStore + ConfsServer）
├── log_client.py        # 统一日志客户端 SDK（与其他服务一致的副本）
├── configs/             # 配置文件存放目录（被分发对象）
│   ├── example.ini      # 示例配置
│   ├── static.ini       # static 服务配置（已接入分发）
│   ├── dnfs.ini         # dnfs 服务配置（已接入分发）
│   └── db.ini           # db 服务配置（已接入分发）
├── docs/
│   └── SECURITY_AUDIT_20260801.md  # 安全审计报告
├── _set.sh              # 初始化脚本（建 venv + 安装依赖）
├── _run.sh              # 启动脚本（venv 中 exec python3 confs.py）
├── requirements.txt     # Python 依赖（本地 ../pymsg + nuitka）
└── README.md
```

## 注意事项

- 配置文件需手动放入 `configs/` 目录（或用 `--dir` 指定其他目录），服务不会自动生成业务配置
- 文件以 UTF-8 读取，非 UTF-8 文件会返回 `配置读取失败` 错误
- 配置名区分大小写（与文件系统一致）
- 单配置文件大小上限为 1 MiB（`MAX_CONFIG_SIZE`），超过此限制会返回 `配置文件过大` 错误，防止超大文件读入内存导致 OOM
- 缓存基于文件 mtime 判断变化，若同秒内修改且 mtime 精度不足（如某些 FAT 文件系统），可能无法及时重载——可手动调 `reload` 路由强制刷新
- `reload` 清空全部缓存的正确方式是**省略 `name` 字段**或传 `{"name": null}`；传空字符串 `""` 会返回错误而非清空缓存（防误操作）
- INI 配置文件中 `%(xxx)s` 语法**不会**被当作插值引用，而是作为字面字符串返回；如需插值请在业务层自行处理
- 调用方拿到 `get()` 返回的 dict 可以自由修改（如 `entry["content"] = "x"`），不会影响 confs 内部缓存
- 当前接入情况见上文"集成关系 → 已接入的服务"；新服务接入可参考 static 的三层 fail-safe 模式（内置默认 → confs 优先 → 本地回退）
- 安全审计报告详见 [docs/SECURITY_AUDIT_20260801.md](docs/SECURITY_AUDIT_20260801.md)
