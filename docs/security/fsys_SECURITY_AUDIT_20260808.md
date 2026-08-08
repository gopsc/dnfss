# fsys 安全审计报告

**审计日期**: 2026-08-08
**审计范围**: `fsys/src/fsys.cpp`（1589 行，修复后）
**对比基准**: `static/fsys.py`（792 行）+ `static_SECURITY_AUDIT_20260801.md`（static 24 项审计结论）+ `dnfs2_SECURITY_AUDIT_20260808.md`
**审计轮次**: 第 1 轮（对照 static 审计逐项验证 + C++ 实现深度复查）
**安全评级**: A-

---

## 1. 审计概述

fsys 是 Python `static` 模块的 C++20 重实现（libhttp + libmsg + boost::json），审计目标：

1. **逐条验证** static 审计（24 项）中的修复在 C++ 重写中是否保留，防止回归；
2. **深度复查** C++ 实现特有的问题（内存模型、libcurl 使用、路径解析、路由机制）。

本轮发现 8 项问题（4 P1 / 1 P2 / 3 P3），**全部已修复**；另记录 6 项已知限制。

---

## 2. 本轮新发现问题（全部已修复）

### F-1: static_root/SSL 相对路径基于可执行文件目录解析（服务无法提供文件） ✅ 已修复

- **等级**: P1（功能性，启动即失效）
- **位置**: `load_config()` 步骤 8（原 L347-369）
- **问题**: 相对路径 `static_root` 通过 `/proc/self/exe` 基于可执行文件目录解析。二进制位于 `fsys/build/fsys`，而 `public` 符号链接位于 `fsys/public`，因此 `./public` 解析为 `fsys/build/./public`（不存在）。更严重的是构造函数 `fs::create_directories()` 会**静默创建空目录**，服务启动正常但所有路由返回 404/500，无告警。
- **修复**: 相对路径改为基于 `fs::current_path()` 解析（`_run.sh` 以模块目录为 cwd 启动，与 Python 版 `BASE_DIR` 语义一致；Docker 使用绝对路径 `STATIC_ROOT=/app/public`，不受影响）。SSL 证书路径同样修复。
- **勘误（2026-08-08 后）**: 原描述中 `fsys/public` 为指向 `../static/public` 的符号链接；现已改为真实目录，内含自检测试页（`index.html` 等），不再与 static 共享内容。

### F-2: favicon 代理 `CURLOPT_FOLLOWLOCATION` 盲跟重定向绕过 SSRF 校验（回归） ✅ 已修复

- **等级**: P1
- **位置**: `_handle_favicon()`
- **问题**: 原实现 `CURLOPT_FOLLOWLOCATION=1L` 让 libcurl 自动跟随重定向，**重定向目标不再经过私有 IP 校验**。攻击者可用公网主机返回 `302 Location: http://127.0.0.1:8005/...` 探测/访问内网服务。Python 版有 `_FaviconRedirectHandler` 逐跳重新校验（static 审计已有机制第 8 条），C++ 重写丢失了该防护，属于**安全回归**。
- **修复**: 禁用自动跟随（`CURLOPT_FOLLOWLOCATION=0L`），提取 `validate_url_ssrf()` 校验函数（scheme/端口/私有 IP），手动逐跳跟随（最多 3 跳），每跳重新校验；同时设置 `CURLOPT_PROTOCOLS_STR="http,https"` 纵深防御。

### F-3: 查询参数与路径缺失 URL 解码（favicon/filelist/video/catch-all 功能性回归） ✅ 已修复

- **等级**: P1（功能性）
- **位置**: `parse_query()`、`_handle_catch_all()`、`_handle_video()`、`_handle_favicon()`
- **问题**: 前端全部使用 `encodeURIComponent`（已核实：`view.js:139` `/api/filelist?path=${encodeURIComponent(currentPath)}`、`dnfs.js:480` `/api/favicon?url=${encodeURIComponent(sp.url)}`、`view.js:73` 面包屑链接），Flask 自动解码。C++ 版不解码导致：
  1. `/api/favicon?url=https%3A%2F%2F...` → scheme 校验失败 → **favicon 功能完全失效**
  2. `/api/filelist?path=a%2Fb` → 按字面名 `a%2Fb` 查找 → **子目录导航失效**
  3. 中文/空格文件名的静态文件访问失效
- **修复**: 新增 `url_decode()`（`%XX` + 查询串 `+`→空格，路径解码不转换 `+`）；`parse_query` 键值均解码；catch-all/video 路径解码；`safe_path_join` 新增 NUL 字节拒绝（防解码 `%00` 截断）。解码后的遍历序列（如 `..%2F..`）仍被 `safe_path_join` 前缀校验拦截，仅解码一次无双解码风险。

### F-4: 静态文件/无 Range 视频全量读入内存（NEW-7 回归） ✅ 已修复（上限防护）

- **等级**: P1
- **位置**: `_handle_catch_all()`、`_handle_root()`、`_handle_video()`
- **问题**: libhttp 响应为 `string_body`，所有文件全量读入内存返回。Python 版 NEW-7 已用 `send_file` 流式修复该 DoS；C++ 重写回归。攻击者请求 public 下大文件（或 `Range: bytes=0-` 全量拉取大视频）可导致内存耗尽。
- **修复**: 新增 `kMaxServeFileSize = 50MB`：
  - 普通文件 >50MB → 413
  - 视频无 Range 全量 >50MB → 413（浏览器拉取媒体必带 Range，实际无感）
  - Range 片段 >50MB → 416（防 `bytes=0-`）
  - 残留限制见 L-1（需 libhttp 支持 file_body/chunked 才能彻底解决）

### F-5: 限速表 `_rate_store` 无界增长 ✅ 已修复

- **等级**: P2
- **位置**: `_check_rate_limit()`
- **问题**: 限速键为 `"hash:" + client_ip()`，XFF 可伪造且无上限。攻击者旋转 XFF 值可令 map 无限增长耗尽内存（DoS）。Python 版有相同问题（未修），C++ 重写有机会修复。
- **修复**: 键数超过 `kRateMaxKeys=65536` 时全局驱逐过期窗口键。

### F-6: Cookie `q4_token` 子串匹配误判 ✅ 已修复

- **等级**: P3
- **位置**: `_handle_dnfs_proxy()`
- **问题**: `cookies.find("q4_token=")` 子串搜索，名为 `x_q4_token=` 的 Cookie 也会命中，可能把非预期 Cookie 值作为 Bearer Token 转发给 dnfs2。
- **修复**: 新增 `get_cookie()` 按 `;` 拆分、trim、精确按名匹配。

### F-7: `sha256_hex` EVP_MD 泄漏窗口 ✅ 已修复

- **等级**: P3
- **位置**: `sha256_hex()`
- **问题**: 释放条件 `hash_len > 0 && md != EVP_sha256()`，digest 失败（hash_len==0）时 `EVP_MD_fetch` 返回的 md 泄漏。
- **修复**: 改用 `fetched` 标志，digest 成败均释放。

### F-8: `CURLOPT_PROTOCOLS` 已弃用（编译错误） ✅ 已修复

- **等级**: P3
- **问题**: libcurl ≥7.85 弃用 `CURLOPT_PROTOCOLS`，`-Werror` 下编译失败。
- **修复**: 改用 `CURLOPT_PROTOCOLS_STR`。

---

## 3. static 审计 24 项逐条验证（防回归检查）

| static 审计项 | fsys 状态 | 验证说明 |
|------|------|------|
| P0-1 无认证密码重置 | ✅ 未重实现 | 危险功能未移植 |
| P0-2/3/4 yhdet OAuth（Token/SSL 跳过/硬编码密钥） | ✅ 未重实现 | 同上 |
| P0-5 直连 SQLite | ✅ | 通过 `_call_db()` RpcClient 调 db2:8005 |
| NEW-1/2 proxy_send_code / PRIVATE_SECRET | ✅ 未重实现 | 同上 |
| P1-1 check_user/hash 限速 | ✅ | 滑动窗口 20/10 次每分钟，XFF 取真实 IP |
| P1-2/NEW-5 路径遍历前缀 bypass | ✅ | `base_str + "/"` 边界校验 + `weakly_canonical`（symlink 逃逸同样拦截） |
| NEW-3 dnfs 代理 CORS 通配 | ✅ | `_apply_cors` 白名单回显 + `Vary: Origin` |
| NEW-6 代理错误泄露内部异常 | ✅ | 统一返回 `{"error": "upstream unavailable"}`，详情仅记日志 |
| NEW-7 大文件全量读内存 | ⚠️→✅ | 本轮回归后由 F-4 上限防护覆盖，残留限制 L-1 |
| NEW-8 api_hash 校验 | ✅ | `json::parse` 失败/非 object/password 非 string 均 400 + 限速 |
| NEW-9 db 单例/retry/env | ✅ | 成员 RpcClient（retry_on_refused=true）+ DB_HOST/DB_PORT env |
| NEW-10 favicon 端口校验 | ✅ | 端口 [1,65535] 校验，IPv6/userinfo 畸形 URL fail-closed 拒绝 |
| NEW-11 CSP unsafe-inline | ⚠️ 继承 | 与 static/dnfs2 一致的已知限制（前端内联脚本依赖），见 L-3 |
| NEW-12 SSL 回退开发服务器 | ✅ 消除 | libhttp 原生 TLS（F-2 改进项） |
| NEW-13 日志单条失败弃批 | ✅ | `_send_log_batch` 单条 catch 后继续 |
| NEW-14 端口 env 未捕获异常 | ✅ | `safe_stoi` 统一校验，main 捕获后打印 `[fsys] fatal` |
| P2-1 Range 边界校验 | ✅ | start/end 范围 + end≥start + stoi 异常处理 |
| P2-2 日志敏感信息 | ✅ | 无密码/Token 入日志 |
| confs retry 强依赖 | ✅ | `retry_on_refused=true` |
| 配置白名单合并 / 必需项校验 | ✅ | INI 解析 + cors_origins/static_root 必需校验 |
| 安全响应头（6 项） | ✅ | `make_secured_response` 内联每个响应 |
| `__` 前缀系统文件过滤 | ✅ | filelist 过滤 |
| 请求体大小限制 | ✅ | `setMaxBodySize(10MB)`，超限 libhttp 返回 413 |
| curl 全局初始化/清理 | ✅ | main 遵循 dnfs2 P3-N9 模式 |

**结论**: static 审计的全部有效修复在 fsys 中均得到保留；NEW-7（F-4）与 favicon 重定向校验（F-2）两处曾发生回归，已在本轮修复。

---

## 4. 行为差异说明（非缺陷）

### D-1: dnfs 代理透传上游状态码（改进）

Python 版 `_proxy_to_dnfs` 用 urllib，上游非 2xx 抛 HTTPError → 一律返回 502；因此前端 `dnfs.js` 中 `res.status === 401 → 跳转登录页` 的逻辑在 Python 版下是**死代码**（凭证过期时收到 502 而非 401）。fsys 透传上游真实状态码（400/401/404/500），使前端 401 跳转逻辑首次生效。前端其余错误处理读取 `body.error`，与状态码无关，无兼容性问题。

### D-2: libhttp 内置 OPTIONS 反射任意 Origin（继承，影响有限）

fsys 仅对 4 个 dnfs 路由注册了显式 OPTIONS handler（走白名单）。其余路由（如 `/api/hash`）的预检落入 libhttp 内置 OPTIONS 处理——该处理反射任意 Origin（libhttp 审计 P2-1，未修复）。由于 fsys 实际响应（POST 结果）不附带 `Access-Control-Allow-Origin` 头，浏览器仍会阻止跨域读取响应，泄露面被封堵；预检放行本身影响有限。根治依赖 libhttp 修复 P2-1。

### D-3: favicon URL 无路径时的构造

Python 版用 `urlparse().netloc`，fsys 用首个 `/` 截取 origin，等价；`http://host?query`（无路径直接带查询串）这类畸形输入在 fsys 中 fail-closed 拒绝（Python 可解析），不影响正常调用（前端始终发送完整 URL）。

---

## 5. 已知限制（记录，不修复）

### L-1: string_body 无流式传输（P2，架构性）

libhttp 响应体为 `string_body`，50MB 上限是缓解而非根治（50MB × 4 线程仍有 200MB 峰值）。彻底修复需 libhttp 支持 `file_body` 或 chunked 响应，与 Python 版 NEW-7 的 send_file 流式能力对齐。

### L-2: 限速依赖 XFF（P2，继承）

- libhttp 未向 handler 暴露 peer IP（`Session::remote_ip_` 仅内部可用），XFF 缺失时限速键回退 `"unknown"`，所有直连客户端共享窗口。Python 版有 `request.remote_addr` 兜底，此处为弱化。
- 绕过 gateway 直连时可伪造 XFF 绕过限速（Python 相同）。生产依赖 gateway/waf 覆写 XFF。
- 建议：libhttp 在 params 或 request 上下文暴露 peer IP。

### L-3: CSP `unsafe-inline`（P3，继承 static NEW-11 / dnfs2 L-7）

前端（dnfs 特殊节点注入的 HTML/JS）依赖内联脚本，nonce 化需前端架构改造。

### L-4: DNS rebinding 理论窗口（P3，继承 static NEW-10）

`is_private_host` 校验与实际 HTTP 连接之间存在 TOCTOU 窗口（重定向逐跳校验同样存在）。彻底修复需将校验所得 IP 绑定到连接（`CURLOPT_RESOLVE`），当前与 Python 版风险等级一致，可接受。

### L-5: favicon 代理无响应缓存

每次请求都实时拉取上游（Python 版同样无服务端缓存，仅有浏览器侧 `Cache-Control: max-age=86400`）。高频访问同一链接卡片会产生重复外联。

### L-6: Makefile `/opt/homebrew` 路径（继承 dnfs2 惯例）

macOS/Linux 双开发环境约定，Linux 编译需覆盖 `INCS/LIBS/RPATH`（本次审计编译即如此执行）。与 dnfs2 Makefile 保持逐字节一致，未单独修改。

---

## 6. 修复文件清单

| 文件 | 修改内容 |
|------|---------|
| `fsys/src/fsys.cpp` | F-1: static_root/SSL 相对路径基于 CWD 解析 |
| `fsys/src/fsys.cpp` | F-2: `validate_url_ssrf()` + favicon 手动逐跳重定向校验 |
| `fsys/src/fsys.cpp` | F-3: `url_decode()` + parse_query/catch-all/video 解码 + NUL 拒绝 |
| `fsys/src/fsys.cpp` | F-4: `kMaxServeFileSize=50MB` 上限（413/416） |
| `fsys/src/fsys.cpp` | F-5: `kRateMaxKeys=65536` 限速表驱逐 |
| `fsys/src/fsys.cpp` | F-6: `get_cookie()` 精确按名解析 |
| `fsys/src/fsys.cpp` | F-7: sha256_hex `fetched` 标志修复泄漏 |
| `fsys/src/fsys.cpp` | F-8: `CURLOPT_PROTOCOLS_STR` |
| `fsys/README.md` | `[directory] root` 相对路径语义说明修正 |

编译验证: `g++ -std=c++20 -O3 -Wall -Werror` 通过，零错误零警告（`-Werror=deprecated-declarations` 下通过）。

运行时冒烟测试（confs/db/dnfs 在线，临时端口 19200，与生产 Python 版 9200 对照）：

| 用例 | 结果 |
|------|------|
| `GET /` / `/health` / filelist 根目录与子目录 | ✓ 200，`static_root` 正确解析到 `fsys/public`（F-1） |
| filelist 编码路径 `path=js` / `games%2F` | ✓ 200（F-3 解码生效） |
| 路径遍历 `/%2e%2e/%2e%2e/etc/passwd` | ✓ 403 |
| favicon 编码公网 URL / 内网 URL | ✓ 200 image/x-icon / 403 forbidden host（F-2/F-3） |
| `POST /api/hash` | ✓ 哈希值与 Python 版逐位一致 |
| `POST /api/check_user` | ✓ 与 Python 版行为一致（当前环境 db Users 查询失败，两版同返 500 query failed，非 fsys 缺陷） |
| dnfs 代理 `/get_notes` | ✓ 透传（上游自身 500，直连上游同果，非代理缺陷） |
| `/video/*` 无 Range 200 / Range 206（Content-Range/Content-Length）/ 越界 416 | ✓（2026-08-08 补测，见下） |

> **补充（2026-08-08）**: 初轮冒烟未覆盖 `/video/*` Range 路径（当时无测试媒体文件）。后在 `fsys/public/` 内置自检测试页（`index.html`，15 项自动检查）及 `test/sample.mp4`（1KB），补齐 Range 用例：全量 200 + `Accept-Ranges: bytes`、`bytes=0-99` → 206 + `Content-Range: bytes 0-99/1024` + `Content-Length: 100`、`bytes=999999-` → 416，均验证通过（F-4 的 416 分支同时覆盖）。

---

## 7. 安全评级

### 评级: A-

**理由**:
- static 审计 24 项修复全部保留，两处回归（F-2 SSRF 重定向、F-4 大文件内存）发现后立即修复
- 4 项 P1 全部修复，编译零警告
- 相比 Python 版新增：请求体限制、TLS 原生支持、限速表驱逐、Cookie 精确解析、401 透传使前端登录跳转生效
- 扣分项：L-1 流式传输缺失（依赖 libhttp 演进）、L-2 限速依赖 XFF（依赖 libhttp 暴露 peer IP）、L-3 CSP unsafe-inline（前端架构限制）

**升级路径**（修复以下任一项可评 A）:
- libhttp 支持 file_body/chunked → 解除 L-1
- libhttp 暴露 peer IP → 解除 L-2
- libhttp 修复 P2-1（OPTIONS Origin 白名单）→ 消除 D-2
