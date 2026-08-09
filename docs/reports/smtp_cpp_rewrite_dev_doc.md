# smtp 模块 C++20 重写 · 开发方案

> 文档类型：模块重写开发计划
> 模块：smtp（邮箱验证码服务）
> 日期：2026-08-09
> 参照：captcha2 / db2 / dnfs2 的 Python → C++20 重写路径

---

## 1. 概述

### 1.1 背景与动机

smtp 模块是 micro_servers 中仅存的少量 Python 内部基础服务之一（smtp + captcha + ai_review），负责注册/重置密码流程的邮箱验证码发送与校验。当前 Python 版（`smtp_v2.py`，317 行）依赖仅 `pymsg` + Python 标准库 `smtplib`/`email`，属于**中等规模、外部 I/O 密集**的重写目标。

| 维度 | Python 版现状 | 重写收益 |
|------|-------------|----------|
| 依赖 | Python venv + pymsg 本地包 | 消除 Python 运行时，编译为单二进制 `smtp2.bin` |
| 启动 | `_run.sh` 激活 venv 后 `exec python3` | 与 db2/dnfs2/captcha2 一致的直接启动 |
| 安全遗留 | P1-F1 now 遮蔽 → 回滚失效、P2-F2 验证码明文入日志、P2-F3 random 弱随机等 7 项审计发现未修复 | 重写时一并根治 |
| 一致性 | 项目已有 4 个成功的 C++ 重写案例（db2/dnfs2/captcha2/fsys） | 与 gateway/db2/dnfs2/captcha2 统一技术栈 |
| SMTP 会话 | `smtplib.SMTP_SSL` 封装良好但异常粒度粗 | 直接控制 OpenSSL 会话，更精细的超时/错误处理 |

### 1.2 设计目标

1. **协议完全兼容**：pymsg 三条路由（`send_email` / `verify_email` / `health`）请求/响应格式与 Python 版字节级一致，gateway 零改动
2. **审计清零**：修复安全审计报告（`smtp_SECURITY_AUDIT_20260801.md`）中全部未修复项
3. **零新增重型依赖**：仅引入项目已安装的 OpenSSL（libssl-dev，各 C++ 模块均依赖）
4. **基础设施复用**：libmsg（RpcServer/AsyncQueue）、boost::json、Conf.hpp，所有已在项目内的组件
5. **安全增强**：`std::random_device` 替代 `random`、常数时间比较、日志脱敏

---

## 2. 现状分析

### 2.1 源码结构

```
smtp/
├── smtp_v2.py           # 317 行，服务主程序
├── log_client.py        # 245 行，异步日志 SDK（项目通用）
├── _set.sh              # venv 创建 + pip install
├── _run.sh              # venv 激活 + exec python3 smtp_v2.py
├── requirements.txt     # ../pymsg + nuitka
└── .gitignore
```

### 2.2 功能清单

| 功能 | 代码位置 (smtp_v2.py) | 行数 | C++ 等效方案 |
|------|----------------------|------|-------------|
| .env 手动加载（4 种格式兼容） | L30–55 | 26 | Conf.hpp 的 ini 解析（与 gateway 一致） |
| 配置常量化 | L66–100 | 35 | `struct SmtpConfig` 编译时常量 + env 覆盖 |
| `SmtpServer.__init__` | L110–120 | 11 | `RpcServer` 构造 + `add_route` 注册 |
| `_handle_send_email` | L145–251 | 107 | SMTP_SSL 客户端 + 配额/冷却/熔断逻辑（~200 行 C++） |
| `_handle_verify_email` | L253–292 | 40 | 线程安全 Store + 常数时间比较（~70 行 C++） |
| `_handle_health` | L294–295 | 2 | 返回 `{"status":"ok","service":"smtp"}` |
| `main` / argparse | L302–316 | 15 | boost::program_options（与 gateway 一致） |
| log_client | 整个文件 | 245 | 基于 libmsg `AsyncQueue`，参照 captcha2 `LogC.hpp`（~60 行） |

### 2.3 依赖替换矩阵

| Python 依赖 | C++ 替代 | 引入方式 |
|------------|---------|----------|
| `smtplib.SMTP_SSL` | 自写 SMTP_SSL 客户端（POSIX socket + OpenSSL BIO） | 系统已安装 `libssl-dev` |
| `email.mime.text.MIMEText` | 手写 MIME 拼装（~20 行字符串模板） | 无额外依赖 |
| `pymsg` | libmsg (`RpcServer` / `AsyncQueue`) | 系统已安装 (`libus_msg.so`) |
| `log_client.py` | 独立 `LogC.hpp`（基于 `AsyncQueue`） | 参照 captcha2 `LogC.hpp` |
| `random` / `secrets` | `<random>` (`random_device` + `mt19937`) | C++11 标准库 |
| `threading.Lock` | `std::mutex` + `std::lock_guard` | C++11 标准库 |
| `json` | `boost::json` | 系统已安装 |
| `argparse` | `boost::program_options` | 系统已安装 |
| 手动 .env 解析 | Conf.hpp（ini 格式） / 环境变量 | 与 gateway 一致 |

### 2.4 SMTP_SSL 会话流程分析

Python `smtplib.SMTP_SSL` 在端口 465 上的标准流程：

```
1. socket.create_connection((host, port), timeout=10)
   → TCP 连接
2. ssl.SSLContext.wrap_socket(sock) 或直接 SMTP_SSL 构造
   → TLS 握手（隐式 TLS，端口 465 专用）
3. server.ehlo() / helo()
   → SMTP 握手，获取服务器能力
4. server.login(user, password)
   → AUTH LOGIN 认证
5. server.sendmail(from_addr, [to_addr], msg.as_string())
   → MAIL FROM → RCPT TO → DATA → 发送 MIME 正文
6. server.quit()
   → QUIT 优雅关闭
```

C++ 实现使用原始 POSIX socket + OpenSSL `BIO`（参照 libhttp HttpClient 的 TLS 栈），约 100 行。

---

## 3. 整体设计

### 3.1 模块结构

```
smtp2/
├── src/
│   └── main.cpp                  # ~550 行，全部实现
│       ├── SmtpConfig             # 配置结构体 + 加载（~40 行）
│       ├── CodeStore              # 线程安全验证码存储（~60 行）
│       ├── SendLimiter            # 双窗口限流 + 全局熔断（~50 行）
│       ├── SmtpClient             # SMTP_SSL 客户端（~100 行）
│       ├── generate_code()        # 安全随机验证码（~10 行）
│       ├── build_mime()           # MIME 邮件拼装（~20 行）
│       ├── route handlers         # 三个路由处理（~150 行）
│       └── main()                 # 参数解析 + 配置 + 启动（~80 行）
├── inc/
│   └── LogC.hpp                   # 简化版异步日志客户端（~60 行，参照 captcha2）
├── Makefile                        # 构建（~30 行）
├── _set.sh                         # make + install（~10 行）
├── _run.sh                         # 启动脚本（~5 行）
└── README.md
```

### 3.2 与现有 C++ 基础库的关系

```
smtp2.bin
    │
    ├── libmsg (RpcServer)        ← pymsg 协议 C++ 实现，路由分发 + 响应包装
    │     └── libus_msg.so        （系统已安装）
    │
    ├── libmsg (AsyncQueue)       ← 异步批量日志推送
    │     └── libus_msg.so
    │
    ├── boost::json               ← JSON 解析/序列化
    │     └── libboost_json       （系统已安装）
    │
    ├── boost::program_options    ← 命令行参数解析
    │     └── libboost_program_options（系统已安装）
    │
    ├── OpenSSL (libssl)          ← SMTP_SSL TLS 会话
    │     └── libssl.so           （系统已安装，各 C++ 模块均依赖）
    │
    └── POSIX socket              ← TCP 连接（标准系统调用）
```

### 3.3 配置体系

与 gateway/db2/dnfs2/captcha2 一致的两层加载：confs 拉取 `.ini`（若存在）→ 环境变量覆盖。

**smtp.ini（新增，托管在 confs/configs/）**：

```ini
[server]
host = 127.0.0.1
port = 8008
workers = 4

[smtp]
server = smtp.163.com
port = 465
user = your@163.com
password = your_smtp_password          # 也支持环境变量 SMTP_PASSWORD 覆盖
mail_from =                            # 可选，默认同 user
code_len = 6
code_ttl = 600
send_interval = 60
max_verify_attempts = 5
email_hourly_limit = 3
email_daily_limit = 10
smtp_hourly_limit = 100
default_subject = DNFS 邮箱验证
```

**环境变量覆盖（最高优先级，Docker 兼容）**：

| 变量 | 对应配置项 |
|------|-----------|
| `SMTP_SERVER` | smtp.server |
| `SMTP_PORT` | smtp.port |
| `SMTP_USER` | smtp.user |
| `SMTP_PASSWORD` | smtp.password |
| `MAIL_FROM` | smtp.mail_from |
| `EMAIL_HOURLY_LIMIT` | smtp.email_hourly_limit |
| `EMAIL_DAILY_LIMIT` | smtp.email_daily_limit |
| `SMTP_HOURLY_LIMIT` | smtp.smtp_hourly_limit |

向后兼容 `__PASSWORD` 环境变量（Python 版历史遗留，作为 `SMTP_PASSWORD` 的 fallback）。

---

## 4. 详细设计

### 4.1 CodeStore — 线程安全验证码存储

```cpp
class CodeStore {
public:
    struct Entry {
        std::string code;          // 6 位数字验证码
        double expires_at;         // unix timestamp（std::chrono → double）
        int attempts = 0;          // 连续校验失败次数
    };

    // 写入验证码（覆盖已有）；同时惰性清理过期条目
    void put(const std::string& email, const std::string& code,
             double ttl_seconds);

    // 校验验证码：正确 → 删除并返回 true；错误 → attempts++，
    // 达 max_attempts 时删除，invalidated 置 true 并返回 false
    bool verify(const std::string& email, const std::string& code,
                double ttl_seconds, int max_attempts,
                bool& invalidated);

    // 删除指定邮箱的验证码（发送失败回滚用）
    void remove(const std::string& email);

    // 惰性清理所有过期条目
    void expire();

private:
    mutable std::mutex mtx_;
    std::unordered_map<std::string, Entry> store_;
};
```

**与 Python 版的对应**：`code_store` dict + `STORE_LOCK`。C++ 版封装为独立类，校验通过后 `store_.erase(it)` 保证一次性消费；`verify()` 使用 `hmac_compare_digest` 风格常数时间比较（审计 P3-F4 修复）。

### 4.2 SendLimiter — 双窗口限流 + 全局熔断

```cpp
class SendLimiter {
public:
    // 检查是否允许发送；允许时预占时间戳（返回 true + 记录 ts_id）。
    // ts_id 用于失败回滚（精确移除预占的时间戳）。
    bool try_consume(const std::string& email,
                     int hourly_limit, int daily_limit,
                     int global_hourly_limit,
                     uint64_t& ts_id);

    // 回滚预占：传入 try_consume 返回的 ts_id，精确移除对应的三条记录
    void rollback(const std::string& email, uint64_t ts_id);

private:
    // send_log[email] = {hour_deque, day_deque}
    // global_send_log = deque<pair<ts_id, timestamp>>
    // 惰性清理：每次 try_consume 时剔除滑出窗口的条目

    mutable std::mutex mtx_;
    struct EmailLog {
        std::deque<double> hour;
        std::deque<double> day;
    };
    std::unordered_map<std::string, EmailLog> send_log_;
    std::deque<std::pair<uint64_t, double>> global_log_;
    uint64_t next_ts_id_ = 1;
};
```

**修复 P1-F1 "now 变量遮蔽"**：C++ 版使用单调递增 `ts_id`（`uint64_t`）标识每次预占，失败回滚时按 `ts_id` 精确移除，彻底消除 Python 版浮点时间戳 `==` 比较的不确定性——这是审计 P1-F1 的根本性修复。

**修复 P3-F6（subject/content 无长度上限）**：`_handle_send_email` 入口处校验 `subject` ≤ 256 字节、`content` ≤ 4096 字节，超限拒绝。

### 4.3 SmtpClient — SMTP_SSL 客户端

核心难点。Python `smtplib.SMTP_SSL` 封装了 TCP + TLS + SMTP 协议。C++ 手写实现约 100 行。

```cpp
class SmtpClient {
public:
    struct Config {
        std::string server;
        int port = 465;
        std::string user;
        std::string password;
        std::string mail_from;  // 空则用 user
        double timeout = 20.0;  // 完整 SMTP 会话超时
    };

    explicit SmtpClient(const Config& cfg);

    // 发送一封邮件；失败抛出 std::runtime_error（含可读错误消息）。
    // 调用方可选择脱敏后返回或记录日志。
    void send(const std::string& to,
              const std::string& subject,
              const std::string& body_mime);

private:
    // 内部：
    // 1. socket() → connect() 创建 TCP 连接
    // 2. SSL_CTX_new(TLS_client_method()) → SSL_new → SSL_set_fd → SSL_connect
    // 3. SMTP 协议交互：
    //    EHLO → AUTH LOGIN → MAIL FROM → RCPT TO → DATA → QUIT
    // 4. 每步读取响应码（2xx/3xx 为成功，4xx/5xx 抛异常）

    Config cfg_;
};
```

**SMTP 协议交互伪代码**：

```cpp
void SmtpClient::send(const std::string& to,
                       const std::string& subject,
                       const std::string& body_mime) {
    // 1. TCP connect
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    // ... resolve host, connect, set SO_RCVTIMEO/SO_SNDTIMEO ...

    // 2. TLS handshake
    SSL_CTX* ctx = SSL_CTX_new(TLS_client_method());
    SSL* ssl = SSL_new(ctx);
    SSL_set_fd(ssl, sock);
    SSL_connect(ssl);

    // 3. SMTP dialogue
    read_response(ssl, 220);                          // greeting
    send_cmd(ssl, "EHLO localhost");  read_response(ssl, 250);
    send_cmd(ssl, "AUTH LOGIN");
    read_response(ssl, 334);                          // Username:
    send_cmd(ssl, base64_encode(cfg_.user));
    read_response(ssl, 334);                          // Password:
    send_cmd(ssl, base64_encode(cfg_.password));
    read_response(ssl, 235);                          // Authentication successful

    send_cmd(ssl, "MAIL FROM:<" + from + ">");  read_response(ssl, 250);
    send_cmd(ssl, "RCPT TO:<" + to + ">");      read_response(ssl, 250);
    send_cmd(ssl, "DATA");                      read_response(ssl, 354);
    send_data(ssl, body_mime + "\r\n.\r\n");    read_response(ssl, 250);

    send_cmd(ssl, "QUIT"); read_response(ssl, 221);

    // 4. cleanup
    SSL_shutdown(ssl); SSL_free(ssl); SSL_CTX_free(ctx);
    close(sock);
}
```

**超时策略**：`SO_RCVTIMEO` + `SO_SNDTIMEO` 设为 20s（与 gateway `_TIMEOUT_SMTP_SEND` 一致）。OpenSSL 的 `SSL_connect` / `SSL_read` / `SSL_write` 遇到 socket 超时会返回错误，被外围 try-catch 捕获并转换为 `std::runtime_error`。

**Base64 编码**：复用 libcrypt `Base64::encode()`（`libus_crypt.so`），免去手写。

**MIME 拼装**（`build_mime()`，~20 行）：
```cpp
std::string build_mime(const std::string& from,
                        const std::string& to,
                        const std::string& subject,
                        const std::string& body_plain) {
    std::ostringstream os;
    os << "From: " << from << "\r\n"
       << "To: " << to << "\r\n"
       << "Subject: =?UTF-8?B?" << base64_encode(subject) << "?=\r\n"
       << "MIME-Version: 1.0\r\n"
       << "Content-Type: text/plain; charset=UTF-8\r\n"
       << "Content-Transfer-Encoding: base64\r\n"
       << "\r\n"
       << wrap_base64_lines(body_plain);  // 每 76 字符换行（RFC 2045）
    return os.str();
}
```

### 4.4 路由处理

```cpp
// send_email 路由
json::value handle_send_email(const json::object& req) {
    // 1. 取出 email → 空或缺 @ → 返回 invalid email
    // 2. SMTP 配置完整性校验（server/user/password 均非空）
    // 3. 发送冷却检查（60s）→ 冷却中返回 retry_after
    // 4. 双窗口邮箱限流 + 全局熔断预占 → 超限返回对应错误
    // 5. 生成验证码 → 写入 CodeStore
    // 6. 调用 SmtpClient.send()
    //    - 成功：刷新冷却时间戳，惰性清理过期冷却/验证码条目
    //    - 失败：回滚 CodeStore、冷却预占、限流预占；返回失败错误
    // 7. 返回 {sent: true, ttl: CODE_TTL}

    // ★ 审计修复清单（在重写中一并解决）：
    // P1-F1：ts_id 精确回滚（§4.2 SendLimiter）
    // P2-F2：日志不打印验证码明文，仅记邮箱 + "验证码发送成功"
    // P2-F3：generate_code() 使用 std::random_device
    // P3-F5：邮箱格式用与 gateway 一致的严格正则
    // P3-F6：subject ≤ 256B, content ≤ 4096B
    // P3-F7：失败响应返回通用文案，SMTP 细节仅入日志
}

// verify_email 路由
json::value handle_verify_email(const json::object& req) {
    // 1. 取出 email + code（兼容旧字段 input_captcha）→ 空值返回 error
    // 2. 调 CodeStore.verify() 常数时间比较 → 返回 valid/invalidated
    // 3. 日志仅记邮箱 + "验证通过" / "验证失败"
    // ★ 审计修复：P3-F4 常数时间比较（hmac_compare_digest 风格）
}

// health 路由
json::value handle_health(const json::object&) {
    return {{"status", "ok"}, {"service", "smtp"}};
}
```

### 4.5 generate_code() — 安全随机验证码

```cpp
std::string generate_code(int len = 6) {
    // thread_local 保证 RpcServer workers>0 时多线程安全
    thread_local std::mt19937 rng(std::random_device{}());
    std::uniform_int_distribution<int> dist(0, 9);

    std::string code;
    code.reserve(len);
    for (int i = 0; i < len; ++i)
        code.push_back('0' + dist(rng));
    return code;
}
```

**审计修复（P2-F3）**：Python 版使用 `random.randint`（MT19937 但种子来自系统时间等确定性源），C++ 版 `std::random_device` 作为种子源，提供真正的非确定性熵（Linux 上映射到 `/dev/urandom`）。

### 4.6 LogC.hpp — 异步日志客户端

与 captcha2 的 `LogC.hpp` 完全一致（参照 `captcha2/inc/LogC.hpp`），基于 libmsg `AsyncQueue<LogEntry>`：
- 异步批量推送（攒满 50 条或 2 秒 flush）
- fail-safe（db 不可用静默丢弃）
- `push()` / `info()` / `warning()` / `error()` API
- 环境变量 `DB_HOST` / `DB_PORT` 覆盖

---

## 5. 实现计划

### 5.1 分阶段交付

| 阶段 | 里程碑 | 关键产出 | 预估工时 |
|------|--------|----------|----------|
| **P0 — 基础设施** | 目录骨架 + Makefile + LogC.hpp | `smtp2/` 结构搭建，能编译出空 `main()` 链接 libmsg + OpenSSL | 0.5h |
| **P1 — SmtpClient** | SMTP_SSL 客户端全链路 | POSIX socket → OpenSSL TLS → SMTP EHLO/AUTH/MAIL/DATA/QUIT 完整会话，独立测试程序可发一封真实邮件 | 4h |
| **P2 — CodeStore + SendLimiter** | 线程安全存储 + 限流熔断 | `CodeStore` 生成/校验/过期/作废 + `SendLimiter` 双窗口/全局熔断/ts_id 精确回滚 | 2h |
| **P3 — 路由集成** | `RpcServer` 三路由 + 命令行参数 | 能与 gateway 联调，pymsg 协议互通 | 1.5h |
| **P4 — 配置加载** | confs smtp.ini 拉取 + env 覆盖 + 向后兼容 | 与 Python 版 `.env` 格式兼容，`__PASSWORD` 别名支持 | 1h |
| **P5 — 审计修复验证** | 逐条对照审计报告确认修复 | P1-F1 / P2-F2 / P2-F3 / P3-F4~F7 全部标绿 | 1h |
| **P6 — 测试** | 黑盒测试 + SMTP mock 测试 | `tests/test_smtp2.py` | 2h |
| **P7 — 文档 + 部署** | README + _set.sh/_run.sh + init.txt 更新 | 完整文档 + 一键安装 + core 自启动 | 0.5h |

**总计预估：~12.5 小时**

### 5.2 P1 SmtpClient 是高风险路径

P1 阶段涉及 OpenSSL TLS 握手与 SMTP 协议状态机，是重写中**唯一的外部网络 I/O 路径**。策略：

1. **独立测试程序**：先写一个 `smtp2/test_send.cpp`，硬编码 SMTP 凭据，独立验证"连接 → TLS → 认证 → 发送 → 断开"全流程
2. **逐响应码验证**：SMTP 服务器返回 220/250/334/235/354/221 的协议序列化，每步 assert
3. **异常路径测试**：错误密码 → 认证失败、错误邮箱 → RCPT TO 拒绝、网络断开 → socket 超时
4. **多服务商兼容**：至少验证 163.com（465 端口隐式 TLS）一种主流配置

### 5.3 构建系统

```makefile
# smtp2/Makefile
CXX      = g++
CXXFLAGS = -std=c++20 -Wall -O2
INCS     = -I./inc
LIBS     = -lus_msg -lboost_json -lboost_program_options -lus_crypt -lssl -lcrypto -lpthread

TARGET   = smtp2.bin
SRC      = src/main.cpp

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCS) $^ $(LIBS) -o $@

install: $(TARGET)
	cp $(TARGET) /usr/local/bin/

clean:
	rm -f $(TARGET)
```

> **说明**：`-lssl -lcrypto` 提供 OpenSSL 的 TLS 和 base64；`-lus_crypt` 备选 base64（libcrypt 已封装），实际按需二选一。无新增系统包。

---

## 6. 测试计划

### 6.1 黑盒测试（`tests/test_smtp2.py`）

复用现有测试框架（`tests/blackbox/helpers.py` 的 `PymsgClient`），覆盖：

| 测试用例 | 说明 |
|----------|------|
| `test_send_email_valid` | 正常发送 → `{"sent": true, "ttl": 600}` |
| `test_send_invalid_email` | 不含 `@` → `{"sent": false, "error": "invalid email"}` |
| `test_send_missing_config` | SMTP 未配 → `{"sent": false, "error": "SMTP 服务未配置"}` |
| `test_send_cooldown` | 60s 内连发两次 → 第二次返回 `retry_after` 且不覆盖验证码 |
| `test_send_email_hourly_limit` | 超过 3 次/小时 → 返回配额耗尽错误 |
| `test_send_email_daily_limit` | 超过 10 次/天 → 返回每日上限错误 |
| `test_send_global_fuse` | 全局 100 次/小时触发 → 返回全局熔断 |
| `test_send_failure_rollback` | Mock SMTP 失败 → 验证码/冷却/配额全部回滚（**P1-F1 回归测试**） |
| `test_verify_correct` | 正确验证码 → `{"valid": true}`（一次性消费） |
| `test_verify_wrong` | 错误验证码 → `{"valid": false, "error": "invalid or expired code"}` |
| `test_verify_replay` | 校验通过后再次校验 → 第二次也应返回 `invalid or expired code` |
| `test_verify_invalidated` | 连续 5 次错误 → `{"valid": false, "invalidated": true}` |
| `test_verify_expired` | 超时后校验 → `{"valid": false, "error": "invalid or expired code"}` |
| `test_verify_missing_fields` | 缺少 email 或 code → `{"valid": false, "error": "email and code required"}` |
| `test_verify_compat_input_captcha` | 使用旧字段 `input_captcha` 传递验证码 → 正常校验 |
| `test_health` | → `{"status": "ok", "service": "smtp"}` |

### 6.2 SMTP Mock 测试

P1-F1 回归测试需要 SMTP 失败的场景，不能依赖真实 SMTP 服务。方案：

1. **独立 mock 程序**（`smtp2/test_mock_smtp.py`）：在本地随机端口启动一个极简 SMTP 服务器（仿 220/250/334/235/354/221 协议但 `RCPT TO` 后返回 550 拒绝），smtp2.bin 连接该 mock 端口
2. **验证链路**：发送请求 → smtp2 连接 mock → mock 返回 550 → smtp2 触发失败回滚 → 验证 store/limiter 中无残留

### 6.3 gateway 联调

1. 启动 smtp2.bin 替代 Python smtp（监听 `127.0.0.1:8008`）
2. `POST /register/send_code` → 正常返回 `{"success": true}`
3. 邮箱收到验证码，`POST /register` 校验通过
4. 重置密码流程（`/reset/send_code` → `/reset/password`）完整走通

---

## 7. 迁移计划

### 7.1 渐进切换

```
阶段 1: smtp2.bin 开发完成，通过全部测试（包括 SMTP mock 测试）
        ↓
阶段 2: 在测试环境与 Python smtp 并行运行（不同端口），全量测试
        ↓
阶段 3: 修改 core/init.txt，将 smtp/_run.sh 替换为 smtp2/_run.sh
        ↓
阶段 4: 生产切换，观察 1 周无异常后删除 Python smtp 目录
```

### 7.2 gateway 零改动保证

- pymsg 协议不变（JSON RPC 约定不变）
- 监听地址不变（`127.0.0.1:8008`）
- 三条路由 `send_email` / `verify_email` / `health` 请求/响应格式字节级兼容
- gateway 的 `handleSendEmailCode()` / `verify_email_code_remote()` / `handleResetSendCode()` 通过 pymsg `MsgClient` 调用，与 Python 版完全兼容

### 7.3 回滚方案

- `init.txt` 中恢复 `bash /opt/micro_servers/smtp/_run.sh`
- Python venv 保持不变（不删除），可立即回滚

### 7.4 配置文件迁移

```
现有：  smtp/.env （gitignore 保护）
新增：  confs/configs/smtp.ini （纳入 confs 统一管理）
兼容：  环境变量 SMTP_* 优先级最高，与 .env 行为一致
平滑：  smtp2 启动时先读 confs smtp.ini → 环境变量覆盖 → 完整性校验
```

---

## 8. 安全考量

### 8.1 审计遗留修复清单

以下 7 项审计发现在 C++ 重写中**一并根治**：

| 编号 | 等级 | 问题 | C++ 版修复 |
|------|------|------|-----------|
| **P1-F1** | P1 | `now` 变量遮蔽 → SMTP 失败时限流/熔断回滚失效 | `SendLimiter` 使用单调 `ts_id` 精确回滚（§4.2） |
| **P2-F2** | P2 | 验证码明文写入集中日志 Logs 表 | 日志仅记邮箱 + 事件名（"验证码发送成功"），不做任何编码记录 |
| **P2-F3** | P2 | `random.randint` 非密码学安全随机 | `std::random_device` 种子 + `std::mt19937`（§4.5） |
| **P3-F4** | P3 | 验证码比较非常数时间 | `std::string_view` 逐字符异或比较（短路无分支）或 `CRYPTO_memcmp`（§4.1） |
| **P3-F5** | P3 | 邮箱格式仅检查 `@` | 与 gateway 一致的正则：`^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$` |
| **P3-F6** | P3 | `subject`/`content` 无长度上限 | 上限 256 / 4096 字节，超限拒绝 |
| **P3-F7** | P3 | 失败响应直传 SMTP 错误原文 | smtp2 自身返回通用文案，SMTP 细节仅入本地日志 |

### 8.2 已有安全机制保持

| 机制 | Python 版 | C++ 版 |
|------|----------|--------|
| 发送冷却 60s | `send_history` + `STORE_LOCK` | `std::unordered_map<email, double>` + `std::mutex` |
| 冷却失败回滚 | `send_history.pop(email, None)` | `cooling_store_.erase(email)` —— 直接 erase，无 P1-F1 风险 |
| 验证码一次性消费 | `del code_store[email]` | `store_.erase(it)` |
| 连续失败 5 次作废 | `entry['attempts'] >= 5 → del` | `entry.attempts >= max → erase + invalidated=true` |
| 10 分钟过期 | 惰性清理（`verify_email` + `send_email` 时） | 同 |
| 邮箱级双窗口限流 | `send_log` + `global_send_log` + 惰性清理 | `SendLimiter`（ts_id 精确回滚） |
| 全局熔断 | `SMTP_HOURLY_LIMIT`（默认 100） | 同 |
| 仅监听 127.0.0.1 | `RpcServer("127.0.0.1", ...)` | 同 |
| 配置缺失不报 value | `missing = [k for k, v ... if not v]` | 同 |
| fail-closed | 异常时 server 不存在 → 校验失败 | `catch (std::exception&)` → 返回 false |

### 8.3 RpcServer 安全特性（继承自 libmsg）

- JSON 解析嵌套深度限制 32 层
- 错误信息脱敏（不泄露内部路径/类型名）
- `recv_timeout` 防 Slowloris（默认 10s）
- Handler 异常不导致服务崩溃

### 8.4 新增安全考量：SMTP 凭据管理

Python 版 SMTP 凭据从 `.env` 文件加载（gitignore 保护）。C++ 版支持两种凭据来源：

1. **confs smtp.ini**（推荐）：纳入 confs 统一管理，凭据与其他配置一起分发
2. **环境变量**（兼容）：`SMTP_PASSWORD` 优先级最高，兼容 Docker secret

`password` 字段仅出现在 `[smtp]` 段，不出现在日志或错误响应中。配置加载失败时报告缺失的 key 名，不打印值。

---

## 9. 新增/修改文件清单

### 新增

```
smtp2/
├── src/
│   └── main.cpp                 # ~550 行，全部实现
├── inc/
│   └── LogC.hpp                 # ~60 行（与 captcha2 共用同一份）
├── Makefile                      # ~30 行
├── _set.sh                       # make + install（~10 行）
├── _run.sh                       # 启动脚本（~5 行）
├── test_send.cpp                 # SmtpClient 独立测试（~50 行）
└── README.md                     # 模块文档
```

### 需修改

| 文件 | 修改内容 |
|------|----------|
| `confs/configs/smtp.ini` | **新增**，smtp 统一配置文件 |
| `core/init.txt` | `smtp/_run.sh` → `smtp2/_run.sh` |
| `docs/INDEX.md` | 添加本文件的索引条目 |

### 零改动

gateway / waf / db / 其他服务均零改动。接口字节级兼容。

---

## 10. 依赖清单

| 依赖 | 用途 | 是否新增 | 安装方式 |
|------|------|----------|----------|
| `libus_msg.so` (libmsg) | RPC 服务端 + 异步日志 | 否 | 已通过 `_inst.sh` 安装 |
| `libboost_json` | JSON 解析 | 否 | 已通过 `_setup.deb.sh` 安装 |
| `libboost_program_options` | 命令行参数 | 否 | 已通过 `_setup.deb.sh` 安装 |
| `libus_crypt.so` (libcrypt) | Base64 编码 | 否 | 已通过 `_inst.sh` 安装 |
| `libssl.so` (OpenSSL) | SMTP_SSL TLS 会话 | 否 | 已通过 `_setup.deb.sh` 安装（`libssl-dev`） |
| `libcrypto.so` (OpenSSL) | 常数时间比较 `CRYPTO_memcmp` | 否 | 随 libssl 安装 |

**零新增系统依赖。** OpenSSL 已是项目所有 C++ 模块的共有依赖（libhttp / libcrypt 均链接 `-lssl -lcrypto`），无须额外 `apt install`。

---

## 11. 与 Python 版的差异汇总

| 项目 | Python 版 | C++ 版 | 影响 |
|------|----------|--------|------|
| 运行时 | Python 3 + venv | 单二进制 `smtp2.bin` | 消除 Python 依赖 |
| 随机数 | `random.randint(0,9)` | `mt19937(random_device())` | 安全增强（P2-F3） |
| 日志内容 | `email: 验证码发送成功(123456)` | `email: 验证码发送成功` | 审计 P2-F2 修复 |
| 限流回滚 | `now` 变量遮蔽 → 预占回滚失效 | `ts_id` 精确匹配回滚 | P1-F1 根治 |
| 验证码比较 | 普通 `==` | `CRYPTO_memcmp` 常数时间 | P3-F4 修复 |
| 邮箱校验 | 仅 `"@" in email` | 与 gateway 一致的严格正则 | P3-F5 修复 |
| 输入长度 | subject/content 无上限 | 256 / 4096 字节硬上限 | P3-F6 修复 |
| 错误响应 | 含 SMTP 服务器原始错误 | 通用文案脱敏 | P3-F7 修复 |
| 进程模型 | Python GIL（仅 `workers=4` 时 I/O 并发受限） | 原生 C++ 多线程 | 真实并发能力 |
| 配置格式 | 手动解析 `.env`（4 种格式） | confs ini + 环境变量覆盖 | 标准化，与 gateway 一致 |
| 凭据管理 | `.env` 文件 | confs smtp.ini 或环境变量 | 纳入 confs 统一管理 |

---

## 12. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| OpenSSL SMTP_SSL 实现有协议细节疏漏（如 AUTH LOGIN 编码、响应码多行处理） | 中 | 高 | P1 独立测试程序 + 至少 163.com 实战验证；参考 `smtplib` 源码的协议状态机 |
| SMTP 响应码 250 含多行文本（如 `250-SIZE 104857600\r\n250 AUTH LOGIN PLAIN`），单行解析丢失信息 | 低 | 中 | 按 RFC 5321 §4.2 实现多行响应解析（`xxx-` 开始，`xxx ` 结束） |
| 不同 SMTP 服务商（163/QQ）TLS 行为差异 | 低 | 中 | P1 阶段多服务商验证；端口 465（隐式 TLS）仅需标准 TLS 握手 |
| gateway 联调时 pymsg 协议兼容性问题 | 极低 | 高 | libmsg 已在 db2/dnfs2/captcha2/fsys 中经过充分验证 |
| `ts_id` 溢出（`uint64_t`，每秒 100 万次可用 58 万年） | 无 | 无 | 理论上不可能 |

### 12.1 SMTP 多行响应处理

Python `smtplib` 内部处理了 RFC 5321 的多行响应格式（`250-SIZE\r\n250-AUTH\r\n250 OK`）。C++ 实现需同等处理：

```cpp
// SMTP 响应 ⇒ 提取最终响应码
// RFC 5321: "250-SIZE\r\n250-AUTH\r\n250 OK" ⇒ 码=250
std::pair<int, std::string> parse_smtp_response(const std::string& raw) {
    // 最后一行格式: "250 OK"（空格分隔，非 '-'）
    // 中间行格式: "250-SIZE"（'-' 表示后续还有续行）
    ...
}
```

在 P1 阶段独立测试程序中加入多行响应的单元测试。

---

## A. 附录：与 captcha2 重写的对比

| 维度 | captcha2 | smtp2 |
|------|----------|-------|
| Python 源码行数 | 299 行 | 317 行 |
| 核心难点 | FreeType 字体渲染 + 像素旋转（CPU 密集，~180 行） | SMTP_SSL 客户端 + 协议状态机（网络 I/O，~100 行） |
| 新增外部依赖 | FreeType（libfreetype-dev） | **零**（全部复用现有依赖） |
| 审计修复项 | P0-1 大小写 / P2-2 tolower UB | **7 项全部未修复**（P1-F1/P2-F2/P2-F3/P3-F4~F7） |
| 预估工时 | 16h | 12.5h |
| 安全随机 | `mt19937(random_device)` | 同 |
| 日志客户端 | LogC.hpp（AsyncQueue） | 同（共用 captcha2 版本） |
| 配置体系 | confs ini + env | confs ini + env（新增向后兼容 `__PASSWORD`） |

smtp2 相比 captcha2 的显著优势：**零新增系统依赖**（OpenSSL 已就位），且解决了 Python 版积压的全部 7 项审计发现，属于"安全债务集中清偿"型重写。

---

## B. 附录：Python smtplib.SMTP_SSL 源码参考路径

- Python 标准库: `Lib/smtplib.py`（`SMTP_SSL` 类，约 L1010-1060）
- 关键方法: `__init__`（socket + SSL wrap）、`login`（AUTH LOGIN 编码）、`sendmail`（MAIL/RCPT/DATA 序列）、`quit`、`docmd`（发送命令并读响应）
- 响应解析: `getreply()` 处理多行 `123-` 续行格式

C++ 实现不需要完全复刻 `smtplib` 的全部功能（如 STARTTLS、8BITMIME），仅需覆盖 smtp 模块实际使用的子集：隐式 TLS（465 端口）、AUTH LOGIN、MAIL FROM/RCPT TO/DATA/QUIT。

---

## C. 附录：SMTP 响应码速查

| 码 | 含义 | 预期阶段 |
|----|------|---------|
| 220 | Service ready | 连接 greeting |
| 250 | OK | EHLO / MAIL FROM / RCPT TO / DATA 结束 |
| 334 | Send auth data | AUTH LOGIN 的用户名/密码提示 |
| 235 | Authentication successful | AUTH 完成 |
| 354 | Start mail input | DATA 后的就绪信号 |
| 221 | Service closing | QUIT |
| 421 | Service unavailable | 临时故障（应重试） |
| 450 | Mailbox unavailable | 邮箱不可用 |
| 535 | Authentication failed | 认证失败 |
| 550 | Mailbox not found | 邮箱不存在 |

所有非 2xx/3xx 响应均为错误，C++ 版逐码抛出含响应行的 `std::runtime_error`，由上层 handler 捕获后脱敏返回。
