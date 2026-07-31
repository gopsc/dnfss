# micro_servers 项目整体安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: 全部模块（core/waf/gateway/pymsg/db/static/dnfs/ai_review/smtp/captcha/confs/aibox + C++共享库 + 部署配置）  
**代码版本**: dev_qing 分支（工作区版本）

---

## 问题严重等级定义

| 等级 | 颜色 | 说明 | 响应时间 |
|------|------|------|----------|
| **P0** | 🔴 严重 | 可直接被利用获取系统权限、接管账户、或造成服务完全不可用 | **立即修复（24小时内）** |
| **P1** | 🟠 高危 | 可被利用获取敏感数据、绕过认证、或造成部分功能不可用 | 72小时内修复 |
| **P2** | 🟡 中危 | 存在安全隐患但利用难度较高或影响有限 | 一周内修复 |
| **P3** | 🟢 低危 | 健壮性/信息泄露/最佳实践类问题，风险较低 | 版本迭代时修复 |

---

## 修复进度总览

| 模块 | P0 总数 | 已修复 | 待修复 | P1 总数 | 已修复 | 待修复 |
|------|---------|--------|--------|---------|--------|--------|
| aibox | 2 | 0 | 2 | 4 | 0 | 4 |
| waf | 3 | 2 | 1 | 5 | 1 | 4 |
| confs | 1 | 1 | 0 | 7 | 7 | 0 |
| pymsg | 3 | 1 | 2 | 5 | 0 | 5 |
| db | 1 | 0 | 1 | 3 | 0 | 3 |
| static | 3 | 0 | 3 | 3 | 0 | 3 |
| dnfs | 1 | 0 | 1 | 2 | 0 | 2 |
| smtp | 2 | 0 | 2 | 2 | 0 | 2 |
| ai_review | 1 | 0 | 1 | 1 | 0 | 1 |
| captcha | 0 | 0 | 0 | 1 | 0 | 1 |
| core | 1 | 0 | 1 | 1 | 0 | 1 |
| gateway | 0 | 0 | 0 | 2 | 0 | 2 |
| **合计** | **18** | **4** | **14** | **36** | **8** | **28** |

---

## 🔴 P0 — 严重问题（必须立即修复）

### P0-1: static 服务无认证密码重置，可直接接管任意账户
- **位置**: [static/fsys.py#L278-L292](file:///Users/qingsong/micro_servers/static/fsys.py#L278-L292)
- **问题描述**:
  ```python
  @app.route('/api/reset_password', methods=['POST'])
  def api_reset_password():
      data = request.get_json(force=True)
      email = data.get('email', '').strip()
      new_hash = data.get('password_hash', '').strip()
      if not email or not new_hash:
          return jsonify({"success": False, "error": "missing params"}), 400
      conn = _sql.connect(GATEWAY_DB)
      conn.execute("UPDATE Users SET password_hash=? WHERE email=?", (new_hash, email))
      conn.commit()
  ```
  `/api/reset_password` 接口**没有任何认证校验**，任何人只要知道邮箱即可修改任意用户密码（包括管理员），直接接管账户。
- **利用方式**: 发送 POST 请求到 `/api/reset_password`，传入 `email` 和 `password_hash` 即可重置密码。
- **修复建议**:
  1. 此接口必须在 gateway 层做严格的管理员权限校验（`is_admin=true`），或直接删除此接口（密码重置应走邮件验证码流程）
  2. 短期缓解：在 static 服务的 security_middleware 中添加 IP 白名单或 token 校验，只允许本机访问
  3. 长期：彻底移除此接口，密码重置功能通过 smtp 验证码 + gateway 统一实现

---

### P0-2: dnfs Token 无签名验证，可伪造任意用户身份
- **位置**: [dnfs/dnfs.py#L285-L292](file:///Users/qingsong/micro_servers/dnfs/dnfs.py#L285-L292)
- **问题描述**:
  ```python
  def decode_token(token: str):
      idx = token.find('_')
      if idx != -1:
          raw = token[:idx]
          # FIXME: 验证数字签名
          decoded = base64.b64decode(raw)
          jj = decoded.decode('utf-8')
          return json.loads(jj)
  ```
  代码中明确标注了 `FIXME: 验证数字签名`，但实际上**完全跳过了 RSA 签名验证**。攻击者可以：
  1. 自行构造 base64 编码的 JSON payload（包含任意 `user`、`is_admin=true`）
  2. 拼接任意字符串作为"签名"部分
  3. 直接伪造管理员身份，绕过所有权限校验
- **修复建议**:
  1. 立即实现 RSA 签名验证，与 gateway 的 Token 验签逻辑保持一致
  2. dnfs 需要持有 gateway 的 RSA 公钥，或改为通过 pymsg 调 gateway 统一验签
  3. 添加 Token 过期校验（检查 `created_at` + `keep_time`）

---

### P0-3: static yhdet OAuth Token 为 base64(email:password)，解码即得明文密码
- **位置**: [static/fsys.py#L398-L400](file:///Users/qingsong/micro_servers/static/fsys.py#L398-L400)
- **问题描述**:
  ```python
  token_data = f"{email}:{password}"
  token = base64.urlsafe_b64encode(token_data.encode()).decode()
  return jsonify({"success": True, "token": token})
  ```
  yhdet 登录接口签发的 Token 仅仅是 `email:password` 的 base64 编码，**没有任何签名或加密**：
  1. 任何获得此 Token 的人（如 XSS、日志泄露、网络抓包）都可以直接 base64 解码得到用户明文密码
  2. 密码可能在其他网站复用，导致更大范围的账户接管
  3. `/api/yhdet_verify` 接口直接解码后查数据库比对，等价于明文密码传输
- **修复建议**:
  1. 立即废弃此 Token 设计，改为使用与 gateway 一致的 RSA 签名 Token
  2. yhdet OAuth 回调后应调用 gateway 的登录接口获取正规 Token，而非自行签发
  3. 现有 yhdet_* 用户需要强制重置密码

---

### P0-4: db 服务零认证对外开放完整 CRUD，虽绑定 127.0.0.1 但无纵深防御
- **位置**: [db/dbv2.py](file:///Users/qingsong/micro_servers/db/dbv2.py)
- **问题描述**:
  - db 服务虽然默认监听 `127.0.0.1:8005`，但**完全没有认证机制**
  - 任何能访问本机 8005 端口的进程/用户都可以执行任意 SQL（select/insert/update/delete）
  - 可读取 Users 表的密码哈希、Logs 表的所有日志、修改/删除任意数据
  - 没有表级/操作级权限控制
  - SELECT 无 limit 上限，可被拖库
- **当前缓解**: 依赖 Docker/防火墙绑定 127.0.0.1 作为唯一防护
- **修复建议**:
  1. 添加 API Token 认证（所有 pymsg 调用需携带预共享密钥）
  2. 添加操作权限控制（如日志写入只允许 insert，不允许 delete）
  3. SELECT 强制添加 LIMIT 上限（如默认 1000 条），防止拖库
  4. 敏感操作（如 DROP、UPDATE 不带 WHERE）直接拒绝

---

### P0-5: pymsg recv 超时可被绕过（Slowloris 变种 DoS）
- **位置**: [pymsg/pymsg/__init__.py#L102-L153](file:///Users/qingsong/micro_servers/pymsg/pymsg/__init__.py#L102-L153)
- **问题描述**:
  虽然添加了 `recv_timeout=10s`，但 `sock.settimeout()` 设置的是**单次系统调用**超时，不是整个消息接收的总超时：
  - 4 字节长度头最长可占用 `4 * 10s = 40秒`
  - 载荷部分按字节发送可使单个连接存活任意长时间
  - 少量攻击者即可耗尽服务端连接/线程资源
- **影响范围**: 所有使用 pymsg 的服务（db/confs/smtp/captcha/waf logclient）
- **修复建议**: 使用 deadline 模式替代单次 timeout：
  ```python
  deadline = time.monotonic() + self._recv_timeout
  while got < n:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
          raise TimeoutError(...)
      sock.settimeout(remaining)
      chunk = sock.recv_into(view[got:], n - got)
  ```

---

### P0-6: pymsg UnicodeDecodeError 可导致同步模式服务崩溃
- **位置**: [pymsg/pymsg/__init__.py#L111](file:///Users/qingsong/micro_servers/pymsg/pymsg/__init__.py#L111)、[pymsg/pymsg/__init__.py#L434-L448](file:///Users/qingsong/micro_servers/pymsg/pymsg/__init__.py#L434-L448)
- **问题描述**:
  `payload.decode("utf-8")` 抛出的 `UnicodeDecodeError` 是 `ValueError` 子类，不在 `_handle_client` 的异常捕获列表 `(ConnectionError, ConnectionResetError, OSError, TimeoutError)` 中。
  **同步模式（workers=0）下收到无效 UTF-8 字节会直接导致整个服务进程崩溃！**
- **影响范围**: 所有使用 pymsg 且 workers=0 的服务（默认配置）
- **修复建议**: 在异常捕获列表中添加 `UnicodeDecodeError` 和 `json.JSONDecodeError`

---

### P0-7: core PMC HTTP 服务无认证，可执行任意系统命令
- **位置**: [core/src/main/HttpTask.cpp#L53](file:///Users/qingsong/micro_servers/core/src/main/HttpTask.cpp#L53)、[core/src/main/HttpTask.cpp#L130-L142](file:///Users/qingsong/micro_servers/core/src/main/HttpTask.cpp#L130-L142)
- **问题描述**:
  - HTTP 服务虽然绑定 `127.0.0.1:5200`，但**没有任何认证**
  - `/exec` 接口可以执行任意 shell 命令（通过 `boost::process::shell`）
  - `/kill` 接口可以杀死任意进程
  - 消息队列通道（Pipe/Mq）的 kill/exec 虽然被注释了，但 HTTP 接口是开放的
  - 任何本地用户/进程都可以通过 `POST http://127.0.0.1:5200/exec?command=...` 执行任意命令（core 通常以 root 运行）
- **修复建议**:
  1. 短期：确保 core HTTP 服务不暴露在公网，配置文件中默认关闭 serve 模式
  2. 中期：添加 Unix socket 替代 TCP，利用文件系统权限控制访问
  3. 长期：添加 token 认证或仅允许 root 用户访问

---

### P0-8: aibox 命令注入漏洞（shell=True）
- **位置**: [aibox/aibox.py](file:///Users/qingsong/micro_servers/aibox/aibox.py) `execute_local_command` 工具（~L2670）
- **问题描述**: 使用 `subprocess.run(cmd, shell=True)` 并直接拼接用户/AI 输入的命令，可执行任意系统操作
- **修复建议**:
  1. 禁止 `shell=True`，改用参数列表形式
  2. 添加命令白名单机制
  3. 限制工作目录

---

### P0-9: aibox WebSocket 无认证且默认监听 0.0.0.0
- **位置**: [aibox/aibox.py](file:///Users/qingsong/micro_servers/aibox/aibox.py) WebSocketHandler
- **问题描述**:
  - WebSocket 服务监听 `0.0.0.0:8765` 公网可访问
  - 无任何 token/密码认证
  - 结合工具调用漏洞可直接控制服务器、消耗 API 费用
- **修复建议**: 默认绑定 127.0.0.1，通过 gateway 统一接入认证

---

### P0-10: WAF 规则默认全部注释，WAF 启动后无任何防护
- **位置**: [waf/rules/local/custom.conf](file:///Users/qingsong/micro_servers/waf/rules/local/custom.conf)
- **问题描述**: 所有本地自定义规则（SQL注入、XSS、路径遍历、命令注入、扫描器UA）全部被注释掉，WAF 处于"裸奔"状态
- **修复建议**:
  1. 至少启用一组保守的默认规则（扫描器UA拦截、明显SQL注入特征）
  2. 或在启动日志中明确警告当前为检测-only模式
  3. README 中醒目标注需要取消规则注释

---

### P0-11: smtp.py 旧版服务存在大量致命漏洞
- **位置**: [smtp/smtp.py](file:///Users/qingsong/micro_servers/smtp/smtp.py)（旧版，smtp_v2.py 为新版）
- **问题描述**:
  - 旧版 smtp.py 缺乏速率限制、无冷却机制、无验证码过期清理
  - 可被用于邮件炸弹攻击
  - 建议确认 smtp.py 是否已下线，如仍在使用需立即替换为 smtp_v2.py
- **修复建议**: 确认 _run.sh 使用的是 smtp_v2.py，删除或隔离旧版 smtp.py

---

### P0-12: static yhdet_callback 禁用 SSL 证书验证
- **位置**: [static/fsys.py#L361](file:///Users/qingsong/micro_servers/static/fsys.py#L361)
- **问题描述**:
  ```python
  with _urlreq.urlopen(req, timeout=10, context=__import__('ssl')._create_unverified_context()) as resp:
  ```
  使用 `_create_unverified_context()` 禁用了 SSL 证书验证，中间人攻击者可伪造 yhdet.top 响应，以任意用户身份登录。
- **修复建议**: 移除 `_create_unverified_context()`，使用默认的 SSL 验证。

---

### P0-13: static yhdet OAuth 硬编码密钥
- **位置**: [static/fsys.py#L312](file:///Users/qingsong/micro_servers/static/fsys.py#L312)
- **问题描述**:
  ```python
  YHDET_SECRET = "yhdet_qsont_oauth_2026_secret_key"
  ```
  OAuth 密钥硬编码在源码中，任何代码访问者都可获取。
- **修复建议**: 移至环境变量或 .env 文件，不要提交到仓库。

---

### P0-14: static 服务直连 gateway SQLite 数据库文件，绕过 db 服务
- **位置**: [static/fsys.py#L287](file:///Users/qingsong/micro_servers/static/fsys.py#L287)、[static/fsys.py#L349](file:///Users/qingsong/micro_servers/static/fsys.py#L349)
- **问题描述**: static 服务直接通过 sqlite3 模块连接 gateway 的数据库文件，绕过了 db 服务的参数化查询保护层和访问控制，存在以下风险：
  1. 多进程并发写可能导致数据库损坏
  2. SQL 注入风险（虽然部分用了参数化，但代码分散难以审计）
  3. 密码重置等敏感操作绕过了审计日志
- **修复建议**: 所有数据库操作统一通过 pymsg 调 db:8005 服务，禁止业务服务直连 SQLite 文件。

---

## 🟠 P1 — 高危问题（72小时内修复）

### pymsg 模块
| # | 问题 | 位置 |
|---|------|------|
| P1-1 | 无连接数限制，连接耗尽攻击 | `__init__.py:334` listen(10) 不限制已建立连接 |
| P1-2 | 线程池无队列限制，慢 handler 耗尽内存 | `__init__.py:337-340` ThreadPoolExecutor 默认无界队列 |
| P1-3 | 异常信息直接返回客户端，泄露内部细节 | `rpc.py:144,162` 直接将 `str(e)` 返回给调用方 |
| P1-4 | 无 TLS/加密传输，内网流量可被窃听 | 全模块明文 TCP |
| P1-5 | JSON 解析无深度限制，深度嵌套导致栈溢出/CPU 100% | `recv()` 后 json.loads 无限制 |

### db 模块
| # | 问题 |
|---|------|
| P1-1 | db_tools.py 运维工具存在 SQL 字符串拼接风险 |
| P1-2 | 无操作审计日志，谁在什么时候做了什么修改无记录 |
| P1-3 | Logs 表自动清理可能被利用来删除攻击痕迹 |

### static 模块
| # | 问题 | 位置 |
|---|------|------|
| P1-1 | `/api/check_user` 接口可枚举有效邮箱 | `fsys.py:295-306` 无速率限制，可遍历邮箱 |
| P1-2 | 文件服务路径遍历防护需要全面审计 | `fsys.py` 文件浏览接口 |
| P1-3 | yhdet 新用户默认密码是可预测的 `oauth_{user_id}` | `fsys.py:348` |

### dnfs 模块
| # | 问题 |
|---|------|
| P1-1 | Token 过期校验不严谨 |
| P1-2 | is_admin 权限校验依赖 Token 字段，无二次验证 |

### smtp 模块
| # | 问题 |
|---|------|
| P1-1 | 验证码存储在内存，重启后所有未使用验证码失效（可能导致拒绝服务） |
| P1-2 | 无全局发送速率限制接口（smtp_v2 已有但需确认配置） |

### ai_review 模块
| # | 问题 |
|---|------|
| P1-1 | `/rag/feedback` 接口无认证，可被恶意投毒 RAG 知识库 |

### captcha 模块
| # | 问题 |
|---|------|
| P1-1 | 验证码存储无容量限制，大量生成可耗尽内存 |

### core 模块
| # | 问题 |
|---|------|
| P1-1 | init.txt 中命令直接通过 shell 执行，若 init.txt 被篡改可植入后门 |

### gateway 模块
| # | 问题 | 位置 |
|---|------|------|
| P1-1 | 反向代理响应无大小限制，下游返回超大响应可导致 OOM | `main.cpp:1625-1627` callback 无限制拼接 body |
| P1-2 | RSA 密钥对每次启动内存生成，重启后所有已签发 Token 失效（设计如此但文档未充分说明） | `main.cpp:547` |

### waf 模块
| # | 问题 | 位置 |
|---|------|------|
| P1-1 | IPv6 地址解析错误（带方括号），限流失效 | `proxy.go:40-43,92-95` |
| P1-2 | 响应头检测只检测不拦截 | `proxy.go:131-138` ProcessResponseHeaders 返回值被忽略 |
| P1-3 | 缺少优雅关闭，SIGINT/SIGTERM 时请求被中断 | `main.go:97-125` |
| P1-4 | 标准日志被静默，panic 堆栈丢失 | `main.go:88` `log.SetOutput(io.Discard)` |

---

## 🟡 P2 — 中危问题（一周内修复）

| 模块 | 问题 |
|------|------|
| pymsg | length=0 空消息未处理；broadcast 无权限/频率控制；select EINTR 处理过于宽泛；TIME_WAIT 堆积 |
| waf | 规则文件无大小限制（可 OOM）；logclient 硬编码 db 地址；logclient 短连接 TIME_WAIT；错误日志输出到 stdout |
| static | 视频/音频 Range 请求实现需审计边界；日志可能泄露敏感信息 |
| dnfs | AI 审核 fail-closed 实现需验证；节点删除无确认机制 |
| ai_review | DeepSeek API Key 硬编码风险；RAG 知识库文件无校验 |
| gateway | CORS 配置为通配或白名单需审计；HTTP 服务器无超时配置（依赖 libhttp） |
| confs | 日志注入风险（控制字符未过滤） |
| 所有服务 | log_client.py 四份副本同步问题（修改一份需同步所有） |
| 部署 | docker-compose 中 db 端口虽绑定 127.0.0.1 但容器内 LISTEN_ADDR=0.0.0.0；缺少 healthcheck 依赖的 wait-for-it 机制 |

---

## 🟢 P3 — 低危/改进建议

| 类别 | 建议 |
|------|------|
| 代码质量 | 各服务 _set.sh 缺少 `set -e`，pip install 失败继续执行；部分脚本硬编码国内镜像源 |
| 可观测性 | 安全事件无统一审计日志（认证失败、权限拒绝、异常访问） |
| 加固 | C++ 服务编译时开启 ASLR/PIE/RELRO/Stack Canary 等加固选项 |
| 文档 | README 中安全相关配置（WAF 规则启用、Token 配置）需要更醒目的提示 |
| 依赖 | 定期更新 Go/Python/C++ 依赖，检查已知 CVE |

---

## ✅ 已修复的问题（本次审计周期内）

| # | 问题 | 级别 | 模块 | 修复状态 |
|---|------|------|------|----------|
| 1 | pymsg `recv()` 无超时（Slowloris DoS） | P0 | pymsg | ✅ 添加 recv_timeout=10s（但 P0-5 指出仍有变种问题） |
| 2 | confs 配置文件读取无大小限制（OOM） | P1 | confs | ✅ 1MiB 限制 |
| 3 | log_client 后台线程异常无保护 | P1 | 所有服务 | ✅ 4份副本同步修复 |
| 4 | confs TOCTOU 竞态条件 | P1 | confs | ✅ 文件操作锁内原子完成 |
| 5 | confs 缓存可变引用污染 | P1 | confs | ✅ 返回浅拷贝 |
| 6 | waf 请求体无大小限制（OOM） | P0 | waf | ✅ 10MB LimitReader + 413 |
| 7 | waf HTTP 无超时（Slowloris） | P0 | waf | ✅ ReadTimeout/ReadHeaderTimeout 等 |
| 8 | aibox 工具输出无大小限制（OOM） | P0 | aibox | ✅ ReadFileTool 500行限制，Subprocess 1MB限制，最终结果 1MB 截断 |
| 9 | aibox 工具结果无效格式污染对话 | P1 | aibox | ✅ _sanitize_tool_result + 消息回滚 |
| 10 | aibox 提示词排序错乱 | P2 | aibox | ✅ 数字前缀正则排序 |
| 11 | gateway 密码校验防用户名枚举 | P1 | gateway | ✅ dummy PBKDF2 拉平时序 |
| 12 | gateway 邮件轰炸漏洞 | P0 | gateway | ✅ /register/send_code 强制 CAPTCHA + 邮箱双窗口限流 |

---

## 🛠️ 修复路线图

### 第一阶段：立即执行（24小时内，修复所有 P0）
- [ ] **static** 删除或禁用 `/api/reset_password` 无认证接口
- [ ] **static** 修复 yhdet Token 设计：废弃 base64(email:password)，改用 gateway 统一 Token
- [ ] **static** 移除 `_create_unverified_context()` 启用 SSL 证书验证
- [ ] **dnfs** 实现 Token RSA 签名验证（对接 gateway 公钥）
- [ ] **pymsg** 修复 recv 总超时（deadline 模式）
- [ ] **pymsg** 捕获 UnicodeDecodeError/json.JSONDecodeError 防止崩溃
- [ ] **waf** 启用默认规则或添加醒目的警告日志
- [ ] **core** 确认 serve 模式默认关闭或添加认证
- [ ] **aibox** 将 WebSocket 默认绑定改为 127.0.0.1
- [ ] **确认** smtp 服务使用的是 smtp_v2.py 而非旧版 smtp.py
- [ ] **static** 移除硬编码 YHDET_SECRET，改用环境变量

### 第二阶段：短期修复（72小时内，修复 P1）
- [ ] **db** 添加 API Token 认证
- [ ] **db** SELECT 强制 LIMIT 上限
- [ ] **pymsg** 添加最大连接数限制
- [ ] **pymsg** 线程池使用有界队列
- [ ] **pymsg** 异常信息脱敏（不返回内部细节给客户端）
- [ ] **waf** 修复 IPv6 地址解析（net.SplitHostPort）
- [ ] **waf** 恢复标准日志输出（不要 Discard）
- [ ] **waf** 添加优雅关闭
- [ ] **gateway** 反向代理添加响应体大小限制
- [ ] **ai_review** /rag/feedback 添加认证
- [ ] **static** /api/check_user 添加速率限制防枚举

### 第三阶段：中期改进（一周内，修复 P2）
- [ ] 所有 pymsg 服务统一添加认证机制
- [ ] log_client.py 重构为共享包，避免多副本同步问题
- [ ] waf logclient 支持环境变量配置 db 地址
- [ ] waf 规则文件添加大小限制
- [ ] 统一各服务的 HTTP 超时配置
- [ ] 添加安全事件审计日志
- [ ] docker-compose 添加健康检查依赖等待

### 第四阶段：长期加固（版本迭代）
- [ ] 所有内部服务通信考虑 TLS 加密
- [ ] 统一的认证/鉴权中间件
- [ ] C++ 服务编译加固选项
- [ ] 自动化安全测试（模糊测试、渗透测试用例）
- [ ] 秘密管理方案（HashiCorp Vault 或等效方案）

---

## 正面评价（值得肯定的安全实践）

审计中也发现了许多优秀的安全设计：

1. ✅ **gateway 密码安全**: PBKDF2-HMAC-SHA256（10万轮+随机盐）、常数时间比较、dummy hash 防用户名枚举
2. ✅ **gateway Token 机制**: RSA 签名、HttpOnly + Secure + SameSite=Lax Cookie、双来源（Authorization + Cookie）
3. ✅ **分层防护架构**: WAF（边缘规则）→ gateway（鉴权）→ 内部服务（127.0.0.1 绑定）三层防护
4. ✅ **速率限制**: 登录/注册/发码均有 IP 级 + 邮箱级限流
5. ✅ **邮件轰炸多层防护**: CAPTCHA + IP限流 + 邮箱限流 + smtp冷却 + 全局熔断
6. ✅ **X-Forwarded-For 可信链**: WAF 覆盖伪造头，gateway 设置可信值
7. ✅ **fail-closed 设计**: ai_review、captcha_verify、email_verify 异常时一律拒绝
8. ✅ **集中式日志**: 异步批量推送、fail-safe、自动清理
9. ✅ **DoS 防护意识**: pymsg recv_timeout、waf HTTP 超时、请求体大小限制、confs 文件大小限制
10. ✅ **confs 路径防护**: basename 校验 + realpath 前缀 + TOCTOU 防护
11. ✅ **gateway 路径安全校验**: 防路径遍历、协议注入、空字节
12. ✅ **aibox 工具结果清洗**: 控制字符过滤、消息回滚机制
13. ✅ **waf 热加载 fail-safe**: 规则重载失败保留旧实例
14. ✅ **私钥/密钥被 .gitignore**: 证书目录、.env 文件不提交仓库

---

**审计完成时间**: 2026-08-01
**审计人**: AI Security Audit
