# gateway

UniServe Framework 的应用层网关，C++20 实现。

**v0.9.0 起为纯转发服务**：认证职责（Token 签发/验签、登录/注册/重置、限流、WS 代理鉴权）已独立为 [ca](../ca/) 模块。请求链路变为 `waf:443 → ca:8443 → gateway:8445 → 内部服务`，gateway 仅监听 loopback，不再验签。

## 职责

- **HTTP 反向代理**：`GET/POST *` 通配路由，按 `Target-Url` 头转发到 `Proxy.opened` 白名单内的内部服务；缺省走 `Proxy.default`（前端静态资源）。
- **存在性不变量（非验签）**：显式携带 `Target-Url` 的受保护请求必须带有 ca 注入的 `X-Auth-User` 头，缺失一律 401——用于拦截"未经 ca 直连 8445"的误配/调试流量，不是安全边界（安全边界是 loopback 监听 + ca 的 strip-then-set）。
- **身份头透传**：`X-Auth-User` / `X-Auth-Admin` / `Authorization` 不在 `is_special_header` 剥离列表中，原样转发给下游（dnfs2/core 等零改动）。
- **XFF 覆写**：剥离客户端伪造的 `X-Forwarded-For`/`X-Real-IP`/`Forwarded`，以收到的 XFF（ca 链路注入）为准重新设置，下游直接信任。

## 信任模型

```
waf:443 ──HTTP──▶ ca:8443 ──(注入 X-Auth-User)──▶ gateway:8445 ──▶ fsys:9200 / dnfs2:8006 / core:5200
                  ▲ 安全边界                     ▲ 仅 loopback
```

- gateway 不持有 RSA 密钥、不验签；身份完全信任 ca 注入的头。
- 因此 **启动强制 loopback 自检**：`Server.address` 非 `127.0.0.1`/`::1`/`localhost` 时拒绝启动（防止误配导致外部直连伪造身份）。
- `db2:8005` 的 `execute` 路由本就无认证，loopback 信任模型与既有基线一致。

## 接口

### `GET/POST *`（反向代理）

```
X-Auth-User: user@example.com        // 受保护请求必需（由 ca 注入）
Target-Url: http://127.0.0.1:8006    // 可选，缺省取 Proxy.default
```

- `Target-Url` 缺省时走 `Proxy.default`（公开，不检查 `X-Auth-User`）。
- 自定义 `Target-Url` 必须在 `Proxy.opened` 白名单中，否则 403。
- 显式 `Target-Url` 且无 `X-Auth-User` → 401。
- `Proxy.closed`（如 db:8005、ai_review:8007）不在 `opened` 中，无法经 gateway 直访。

错误响应统一脱敏：`HttpException` 仅返回纯消息（内部前缀留服务端日志），其余异常一律 `500 {"success": false, "message": "Internal Server Error"}`。

## 配置

两层加载：confs 拉取（pymsg `get`，`retry_on_refused=true`）→ 环境变量覆盖。

配置文件：[core/configs/gateway.ini](../core/configs/gateway.ini)。

- `Server.address`/`port`/`tls`：默认 `127.0.0.1:8445`（v0.9.0 起 8443 归 ca）；env `LISTEN_ADDR`/`LISTEN_PORT`。
- `Proxy.default` / `Proxy.opened` / `Proxy.closed`：转发目标与白名单（分号分隔）。
- `Downstream.db_host`/`db_port`：日志推送端点（env `DB_HOST`/`DB_PORT`）。

## 编译与运行

```bash
bash _set.sh   # make clean && make
bash _run.sh   # 从 confs 拉取配置启动
```

命令行参数：`-h/--help`、`-v/--version`。日志经 LogC 异步批量推送 db:8005 `Logs` 表（service 名 `gateway`）。

## 目录结构

```
gateway/
├── src/
│   └── main.cpp          # 全部实现：配置加载、存在性不变量、反向代理（约 560 行）
├── inc/
│   ├── LogC.hpp          # 统一日志客户端（异步批量，fail-safe）
│   ├── Conf.hpp          # ConfIni：ini 解析 + env 覆盖
│   ├── ExpBase.hpp       # 异常基类（带 source_location）
│   ├── DbC.hpp           # （保留备用）db 服务客户端
│   └── LocalTime.hpp     # （保留备用）本地时间工具
├── Makefile
├── _set.sh / _run.sh
└── README.md
```

## 版本

`0.9.0`（见 `src/main.cpp`）。启动时打印：

```
Gateway 0.9.0 started
```

近期变更：

- `0.9.0`：**认证独立化瘦身**——认证资产（Token 签发/验签、`/login` `/register` `/logout` `/refresh_token` `/whoami` `/admin/verify` `/captcha/image` `/reset_*` 全部 handler、限流、WS 代理鉴权、PBKDF2/RSA）整体迁至新模块 [ca](../ca/)（v1.0.0，逐字保留实现）；gateway 退居 `127.0.0.1:8445` 纯转发（8443 由 ca 接管，WAF upstream 不变）；新增启动 loopback 强制自检与 `X-Auth-User` 存在性不变量（显式 `Target-Url` 无该头 → 401）；移除对 libcrypt/OpenSSL/sqlite3 的运行时依赖（编译仍链接 libus_crypt 库）。黑盒 tests/blackbox/test_gateway.py 34 断言对 8443（现为 ca）原样全绿。
- `0.8.0`：**P0 安全问题全部修复**（安全评级 B+ → A-）——HttpException 错误消息脱敏（4 处内部步骤消息替换为用户友好文案，修正注册失败状态码 400→500）；**进程退出 bug 修复**——信号处理改为 async-signal-safe（仅设 `volatile sig_atomic_t` 标志 + `write()` 输出）、新增 SIGPIPE 忽略、stop() 添加 8 秒超时保护 + 日志 3 秒超时（保证 11 秒内必定退出）、服务器事件循环改在独立线程运行；Token 双轨吊销机制（jti 黑名单 + 用户版本号）、错误信息脱敏（不返回源码路径/C++ 类型名）、JTI 黑名单内存有界保护（5000 条阈值 + 过期清理）、反向代理响应大小限制（libhttp 默认 10MB）、集中式异步日志客户端 LogC、WebSocket 代理（管理员权限校验）、多层速率限制（IP 级 + 邮箱级）、PBKDF2-HMAC-SHA256 密码哈希（10 万轮）、RSA Token 签名验签、HttpOnly+Secure+SameSite Cookie、密码强度校验。
- `0.7.0`：配置体系完全迁移到 confs 统一配置服务——配置文件统一存放于 `confs/configs/gateway.ini`，删除本地 example.ini，移除 `-i/--input` 命令行参数；启动时从 confs 通过 pymsg `get` 路由拉取配置（`retry_on_refused=true`）；`ConfIni` 增加 `fromString()`/`env()`/`env_int()`；环境变量覆盖；配置完整性校验；`--version/-v` 选项补全实现。
- `0.6.1`：pymsg 客户端增加 `retry_on_refused` 重试机制——新增 `MsgConnectionRefusedException`；`MsgClient::call()` 新增 `retry_on_refused`/`retry_interval` 参数；`DbC::call_msg()` 启用重试等待 db 就绪；`LogC::_send()` 显式不重试（fail-safe）。
- `0.6.0`：WebSocket 反向代理（仅管理员）——新增 `[WsProxy]` 配置段；握手阶段校验 Token 中 `is_admin=true`，通过后双向透明桥接文本/二进制帧到后端。依赖 libhttp v3.1+ 的 `ws_proxy()` 接口。（v0.9.0 起迁至 ca）
- `0.5.4`：修复邮件轰炸漏洞（审计报告 P0-1）——`/register/send_code` 强制 CAPTCHA 校验，新增邮箱级双窗口限流；smtp 层同步增加邮箱级长期限流兜底 + 全局熔断。（v0.9.0 起迁至 ca）
- `0.5.3`：配合 smtp 同邮箱 60 秒发送冷却——`/register/send_code` 在 smtp 返回 `retry_after` 时按 429 透传其提示文案。
- `0.5.2`：限流错误码统一为 429；登录时用户不存在不再兜底 500——空结果统一走 401，并执行 dummy PBKDF2 校验拉平时序防用户名枚举；pymsg 下游调用超时常量化。
- `0.5.1`：登录成功后通过 db 更新 `Users.last_login`（`"$now"` 哨兵）。

## 依赖

- C++20（`g++`）
- Boost：`boost::json`、`boost::program_options`、`boost::property_tree`
- libcurl（反向代理出站请求）
- 项目库：[libhttp](../libhttp/)（HttpServer/HttpClient）、[libmsg](../libmsg/)
