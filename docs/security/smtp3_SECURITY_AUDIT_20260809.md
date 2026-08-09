# smtp3 模块安全审计报告

**审计日期**: 2026-08-09
**审计范围**: `smtp3/src/main.cpp`、`smtp3/inc/LogC.hpp`、`smtp3/Makefile`、`smtp3/_run.sh`、`smtp3/_set.sh`、`confs/configs/smtp.ini`
**代码版本**: `main` @ `6bd0fca`
**审计方式**: 静态代码审查 + 可疑逻辑最小化复现验证（本机编译执行）+ 与 Python 版 `smtp_v2.py`、gateway 调用方、libmsg 基础库交叉比对
**参照文档**: [smtp_SECURITY_AUDIT_20260801.md](smtp_SECURITY_AUDIT_20260801.md)（Python 版审计，含 0809 复审）、[smtp_cpp_rewrite_dev_doc.md](../reports/smtp_cpp_rewrite_dev_doc.md)（重写开发方案）

---

## 一、结论速览

| 等级 | 数量 | 已修复 | 说明 |
|------|------|--------|------|
| P0 | 2 | 2 ✅ | SMTP 响应解析逻辑颠倒（发送 100% 失败）、base64 符号扩展（UTF-8 内容乱码） |
| P1 | 2 | 1 ✅ | P1-2 SIGPIPE 已修；P1-1 TLS 无证书校验/无 SNI 待修 |
| P2 | 1 | 1 ✅ | SMTP 响应读取无大小上限（随 P0-1 一并修复） |
| P3 | 5 | 5 ✅ | 全部修复：P3-2 随 P0-1；P3-1/3/4/5 于 2026-08-09 修复并经运行时验证 |

**总体结论**: smtp3 对 Python 版 7 项审计遗留（P1-F1 ~ P3-F7）的修复**全部落实且实现正确**（见 §七），路由协议与响应格式与 gateway 调用方**完全兼容**（见 §八）。但 `SmtpClient`（重写中新手写的 SMTP_SSL 客户端）存在 **2 个 P0 级功能阻断缺陷**——本审计已在本机编译复现实证：**审计时点的二进制不具备发出任何一封邮件的能力**。此前的重写验证只覆盖了编译、启动、confs 拉取与 `--help`，未覆盖真实 SMTP 会话（smtp.ini 凭据为空），导致 P0 未被发现。

**修复进展（2026-08-09 当日）**: P0-1 / P0-2 已修复并重建二进制，顺带关闭 P2-1 / P3-2；P1-2（SIGPIPE）已修复并经运行时信号投递验证；P3-1 / P3-3 / P3-4 / P3-5 已修复并经运行时验证（日志脱敏实测、LogC 长连接写入 db 实测）；修复代码经 14 项逻辑验证用例全部通过（见 §十一）。**剩余：P0/P1-2/P2/P3 已全部关闭；上线阻断条件收敛为：P1-1（TLS 校验）修复 + 真实 SMTP 账号端到端发信验证**，在此之前仍禁止用 smtp3 替换 Python 版 smtp。

**部署现状**（截至本审计）：`core/init.txt` 仍拉起 Python 版 `smtp/_run.sh`；`_inst.sh` 仅含 `smtp` 未含 `smtp3`；`confs/configs/smtp.ini` 的 `server/user/password` 为空。smtp3 尚未上线，上述 P0 未对生产造成实际影响。

---

## 二、🔴 P0 — 致命问题（发布阻断）

### P0-1: `read_response` 行解析逻辑颠倒，所有 SMTP 会话必然阻塞超时，send_email 100% 失败 ✅ 已修复

- **位置**: `src/main.cpp` L567-599（`SmtpClient::read_response`）
- **问题**: 判定"已收到最终响应行"的逻辑是取 `full.rfind("\r\n")` **之后**的内容作为最后一行：

  ```cpp
  auto last_crlf = full.rfind("\r\n");
  if (last_crlf != std::string::npos) {
      std::string last_line = full.substr(last_crlf + 2);   // ← 取尾部 CRLF 之后
      if (last_line.size() >= 4 && last_line[3] == ' ') { ... return full; }
  }
  // 否则继续 SSL_read
  ```

  SMTP 响应每行**以 CRLF 结尾**（RFC 5321 §4.2），完整响应 `"220 OK\r\n"` 的 `rfind("\r\n")` 命中的是**尾部** CRLF，`last_line` 为空串 → 判定"未读完" → 继续 `SSL_read` → 服务器不会再发数据 → 阻塞至 20 秒 socket 超时 → 抛 `SSL_read 失败`。逻辑完全反了：只有**残缺**的 TCP 分包（最终行尚未收到尾部 CRLF）才会被判定为"读完"。
- **实证**（提取原逻辑最小化复现，2026-08-09 本机执行）:

  | 输入（模拟一次 SSL_read 收到的数据） | 实际判定 | 正确判定 |
  |---|---|---|
  | `"220 OK\r\n"`（标准单行完整响应） | ❌ 继续读 → 超时 | 应返回 |
  | `"250-A\r\n250 OK\r\n"`（多行完整响应） | ❌ 继续读 → 超时 | 应返回 |
  | `"250-A\r\n250 OK"`（无尾 CRLF 的残缺包） | ⚠️ 提前返回 | 应继续读 |

- **影响**:
  1. **功能阻断**: 第一步 `read_response(ssl, 220)`（greeting）即挂死，任何配置下 send_email 均在 ~20s 后以"验证码发送失败"告终 → 注册 / 重置密码全流程不可用。
  2. **DoS 放大**: 每个 send_email 请求占住一个 worker 线程 ~20s；`workers = 4` 时 **4 个并发请求即可占满全部 worker**，期间 `health` / `verify_email` 全部无响应。且发送失败会回滚冷却与限流预占（"失败不计入配额"语义），客户端可无限重试，阻断可持续维持。
- **修复建议**: 改为按**完整行**解析——查找每个以 CRLF 结尾的行，检查**最后一个完整行**是否匹配 `^\d{3} `（第 4 字符为空格即最终行，`-` 为续行）；缓冲区尾部未闭合的数据视为未完成继续读。修复后必须用真实 SMTP 账号完成一次端到端发信验证。
- **修复记录（2026-08-09）**: 已按建议重写为完整行解析（`main.cpp` `read_response`），响应码改逐字符数字校验（顺带关闭 P3-2），累计缓冲加 64KB 上限（顺带关闭 P2-1）。验证：单行/多行完整响应正确返回，残缺包/续行/空缓冲正确续读，码不匹配走异常路径，14 项用例全部通过；`-Wall -Wextra` 零警告重建 `smtp3.bin`。端到端真实发信验证待 smtp.ini 凭据就绪后执行。

### P0-2: `base64_encode` 对 `char` 符号扩展，所有含非 ASCII 字节的输入编码损坏（默认中文文案即中招） ✅ 已修复

- **位置**: `src/main.cpp` L80-94（`base64_encode`），调用方 L473-475（AUTH LOGIN）、L645-646（MIME 主题/正文）
- **问题**: `uint32_t n = (uint32_t(data[i]) << 16)` —— `data[i]` 是 `char`（x86-64 Linux 默认有符号），字节 ≥ `0x80` 时先符号扩展为 `0xFFFFFFxx` 再移位，高位污染同组其余字节：

  ```cpp
  uint32_t n = (uint32_t(data[i]) << 16);          // 0xE9 → 0xFFFFFFE9 → 0xFFE90000
  if (i + 1 < data.size()) n |= (uint32_t(data[i + 1]) << 8);  // OR 进一步互相污染
  ```

- **实证**（原函数最小化复现，2026-08-09 本机执行）:

  | 输入 | 实际输出 | 正确输出 |
  |---|---|---|
  | `"Hello"`（纯 ASCII） | `SGVsbG8=` ✅ | `SGVsbG8=` |
  | `"DNFS 邮箱验证"`（生产默认主题） | `RE5G///p///n///p///o/4E=` ❌ | `RE5GUyDpgq7nrrHpqozor4E=` |

- **影响**:
  1. **默认主题与正文均为中文**（`"DNFS 邮箱验证"` / `"DNFS: 您的邮箱验证码为 {code}，10 分钟内有效。"`），base64 损坏后邮件客户端解码出乱码/解码失败——**即使 P0-1 修复，用户也拿不到可读的验证码**；
  2. **AUTH LOGIN 凭据受损**: 若 SMTP 账号或密码含非 ASCII 字符（部分服务商授权码允许中文），`base64_encode(user/password)` 输出错误，认证必然失败且被脱敏文案掩盖；
  3. 纯 ASCII 内容（如英文 subject）不受影响——这是该缺陷隐蔽的原因，但本项目默认配置恰好全部命中。
- **修复建议**: 移位前统一转无符号：`uint32_t(data[i] & 0xFF)`（或 `unsigned char` 强转），三处移位全部适用。修复后以 `"DNFS 邮箱验证"` 等 UTF-8 样例做编码往返单测（与 Python `base64.b64encode` 输出逐字节比对）。
- **修复记录（2026-08-09）**: 三处移位已改为 `uint32_t(uint8_t(data[i]))`。验证：`"DNFS 邮箱验证"`、中文正文（含验证码替换后形态）、全高位字节 `\xE9\xE9\xE9`、填充边界共 5 项用例与 Python `base64.b64encode` 基准逐字节一致，全部通过。

---

## 三、🟠 P1 — 高危问题

### P1-1: TLS 客户端无证书校验、无主机名校验、无 SNI、无最低版本钳制

- **位置**: `src/main.cpp` L457-463（`SmtpClient::send` TLS 握手段）
- **问题**: 握手只做 `SSL_CTX_new` → `SSL_new` → `SSL_set_fd` → `SSL_connect`，全程：
  - 未调用 `SSL_CTX_set_default_verify_paths` + `SSL_CTX_set_verify(VERIFY_PEER)` → **不校验服务器证书链**；
  - 未调用 `SSL_set1_host` / `X509_VERIFY_PARAM_set1_host` → **不校验证书主机名**；
  - 未调用 `SSL_set_tlsext_host_name` → **不发送 SNI**（多租户 SMTP 端点可能握手失败或拿到错误证书）；
  - 未钳制最低 TLS 版本（`SSL_CTX_set_min_proto_version`）。
- **影响**: SMTP 出站链路存在主动中间人时，攻击者可完整截获 `AUTH LOGIN` 凭据（base64 明文语义）与验证码正文，且客户端毫无感知。Python 版 `smtplib.SMTP_SSL` 在 Python 3.4+ 同样默认不校验证书（同源缺陷，见 smtp 版审计），故本项**不属于回归**，但重写是消除该缺陷的正确时机。
- **修复建议**: 加载系统 CA（`SSL_CTX_set_default_verify_paths`），`SSL_set1_host(ssl, server)`，`SSL_set_tlsext_host_name`，`SSL_CTX_set_min_proto_version(TLS1_2_VERSION)`。如需兼容自签证书可加配置项 `verify_cert = on/off`（默认 on）。

### P1-2: 未忽略 SIGPIPE，SMTP 会话中途被服务器断开时整个 smtp3 进程被内核终止 ✅ 已修复

- **位置**: `src/main.cpp`（全文无信号处理）；对比 `gateway/src/main.cpp` L773-804（显式 `sigaction(SIGPIPE, SIG_IGN)` 并有专门注释说明该风险）
- **问题**: `SmtpClient` 经裸 socket 做 `SSL_write`（未带 `MSG_NOSIGNAL` 语义）。若 SMTP 服务器在会话中途 RST/关闭连接（超时、限流、反垃圾策略均可能），下一次 `SSL_write` 触发 SIGPIPE → **默认行为是终止整个进程**，而非仅让该次发送失败。Python 解释器默认忽略 SIGPIPE，故 Python 版不存在此问题——本项是 C++ 重写**新引入**的缺陷。
- **影响**: 单次发送失败升级为全站邮件服务（含 verify_email / health）整体下线；libmsg 的 `MsgClient::send` 虽带 `MSG_NOSIGNAL`，但只覆盖 pymsg 侧连接，不覆盖 SMTP 出站连接。core 守护会拉起进程，但窗口期内内存中的验证码与限流状态全部丢失。
- **修复建议**: `main()` 开头添加 `signal(SIGPIPE, SIG_IGN)`（与 gateway 做法一致），或在 socket 层改用带错误检测的发送路径。
- **修复记录（2026-08-09）**: `main()` 入口已加 `std::signal(SIGPIPE, SIG_IGN)`（含 `<csignal>` 头）。验证：重建二进制后启动进程并向其投递 SIGPIPE，进程存活（修复前默认行为为终止）；零警告编译，`--help` 正常。

---

## 四、🟡 P2 — 中危问题

### P2-1: `read_response` 累积缓冲无大小上限，异常 SMTP 服务器可致内存耗尽 ✅ 已修复（随 P0-1）

- **位置**: `src/main.cpp` L567-577（`full += buf` 无界循环）
- **问题**: 循环 `SSL_read` 累积进 `std::string full`，直到解析出最终响应行或超时。若（恶意或被入侵的、由运营者配置的）SMTP 服务器持续发送不含 `"NNN "` 终止行的数据，20 秒 socket 超时窗口内（且 SSL_read 每次收到数据即重置超时，实际可远超 20 秒）`full` 无界增长。
- **影响**: 内存耗尽型 DoS。SMTP 服务器为运营者配置的受信目标，故定 P2；修复成本极低。
- **修复建议**: 设 `full` 上限（如 64KB，远超任何正常 SMTP 响应），超限即抛异常。
- **修复记录（2026-08-09）**: 已随 P0-1 修复一并加入 64KB 上限，超限抛异常进入发送失败回滚路径。

---

## 五、🔵 P3 — 低危 / 健壮性问题

### P3-1: `verify_email` 路由 email 未校验直接入日志，存在日志注入 ✅ 已修复
- **位置**: `src/main.cpp` L921、L927、L936
- **问题**: `send_email` 有严格邮箱正则（P3-F5），但 `verify_email` 对任意字符串（含 `\r\n` 等控制字符）原样拼入日志消息写入 db Logs 表。gateway 传入的 email 已经过校验，直接 pymsg 调用（127.0.0.1 信任域内）则未校验。
- **影响**: 低——攻击者需已处于本机信任域；日志按行检索时可能被伪造行干扰。
- **修复建议**: 日志拼接前剥离控制字符，或复用 send_email 的邮箱正则做入口校验。
- **修复记录（2026-08-09）**: 新增 `sanitize_for_log`（控制字符/DEL 替换为 `?`），`verify_email` 三处日志均经脱敏。实测：email 传 `evil\r\nINJECTED`，db Logs 表落库为 `evil??INJECTED`，无法伪造新日志行。

### P3-2: `read_response` 内 `std::stoi` 未捕获异常，非数字响应行导致不可诊断错误 ✅ 已修复（随 P0-1）
- **位置**: `src/main.cpp` L586
- **问题**: 若服务器最终行第 4 字符恰为空格但前 3 字符非数字（畸形响应），`stoi` 抛 `invalid_argument`，错误路径无响应内容上下文。
- **修复建议**: 先逐字符校验 `isdigit`，非数字按协议错误处理并携带原始行。
- **修复记录（2026-08-09）**: 已随 P0-1 修复改为逐字符 `isdigit` 校验 + 手工解码，畸形行不再抛异常而是继续读（由 64KB 上限兜底）。

### P3-3: `connect_to_server` 硬编码 `AF_INET`，仅支持 IPv4 ✅ 已修复
- **位置**: `src/main.cpp` L506
- **问题**: 双栈环境下若 SMTP 服务器仅有 AAAA 记录则解析失败。Python `smtplib` 支持 IPv6，存在轻微行为差异。
- **修复建议**: `AF_UNSPEC`。
- **修复记录（2026-08-09）**: `ai_family` 改为 `AF_UNSPEC`，后续 `getaddrinfo` 结果链表按序尝试连接，IPv4/IPv6 均可达。

### P3-4: `generate_code` 存在模偏差 ✅ 已修复
- **位置**: `src/main.cpp` L633（`rd() % 10`）
- **问题**: `std::random_device` 返回 32 位值，`% 10` 使数字 0-5 的出现概率比 6-9 高约 2/2³²——偏差量级可忽略，但修复成本同样可忽略。
- **修复建议**: `std::uniform_int_distribution<int>(0, 9)`。
- **修复记录（2026-08-09）**: 改用 `std::uniform_int_distribution<int>(0, 9)`，模偏差消除。

### P3-5: LogC 每批日志逐条同步 RPC，且每批新建 TCP 连接 ✅ 已修复（连接复用部分）
- **位置**: `inc/LogC.hpp` L80-99
- **问题**: 与 captcha2 审计 P3-4 同源（该模式自 captcha2 移植而来）：一批最多 50 条日志逐条 `call()`，且每批新建一次 db 连接。
- **影响**: 性能开销，无安全影响；fail-safe 语义正确。
- **修复建议**: 批量 INSERT 或连接复用（可留待 LogC 统一重构时一并处理，db2/captcha2/smtp3 共享同一模式）。
- **修复记录（2026-08-09）**: LogC 改用底层 `detail::openConnection` + `pmsg_send/pmsg_recv` 长连接（与 `MsgClient::call` 线路格式一致，无前缀），跨批复用同一 fd，故障时关闭重建、本批丢弃（fail-safe 不变）。实测：启动与 verify_email 日志经长连接成功落库 db Logs 表。逐条 RPC 改批量 INSERT 需 db 侧协议配合，留待 LogC 统一重构（captcha2/db2 同源模式）。

---

## 六、✅ 已有安全机制（14 项）

| # | 机制 | 位置 |
|---|------|------|
| 1 | 常数时间验证码比较（XOR 累积，防时序侧信道） | `CodeStore::const_time_eq` L307-313 |
| 2 | 验证码一次性消费，防重放 | `CodeStore::verify` L269-271 |
| 3 | 连续失败 5 次作废（防 6 位数字码在线枚举） | `CodeStore::verify` L275-279 |
| 4 | 惰性过期清理，内存无界增长防护 | `CodeStore::expire_locked` L296-304 |
| 5 | 邮箱级双窗口限流（3/h + 10/d）+ 全局熔断（100/h） | `SendLimiter` L323-426 |
| 6 | `ts_id` 精确回滚（根治 Python 版 P1-F1 的浮点比较回滚失效） | `SendLimiter::rollback` L364-377 |
| 7 | 同邮箱 60s 发送冷却，预占 + 失败回滚，并发防重 | `send_email` 路由 L792-827 |
| 8 | `std::random_device` 生成验证码（修复 Python 版 P2-F3） | `generate_code` L628-635 |
| 9 | 日志不打印验证码明文（修复 Python 版 P2-F2，对比 Python L250 明文入日志） | L884 |
| 10 | 失败响应脱敏，SMTP 错误细节仅入服务端日志（修复 Python 版 P3-F7） | L858-865 |
| 11 | 严格邮箱正则，与 gateway 对齐（修复 Python 版 P3-F5） | L752 |
| 12 | subject ≤256B / content ≤4KB 长度上限（修复 Python 版 P3-F6） | L772-789 |
| 13 | 配置白名单合并（仅识别已知 section/key），confs 强依赖阻塞等待，凭据缺失仅警告不阻断 | `merge_ini` / `validate_config` |
| 14 | pymsg 侧 EPIPE 防护继承自 libmsg `MSG_NOSIGNAL`；日志 fail-safe（db 不可用静默丢弃） | `libmsg/Msg.hpp` L234、`LogC.hpp` |

---

## 七、与 Python 版审计 7 项遗留的对照

| 编号 | Python 版缺陷 | smtp3 落实情况 | 复核结论 |
|------|--------------|----------------|----------|
| P1-F1 | `now` 变量遮蔽 + 浮点 `==` 比较导致发送失败时限流/熔断回滚失效 | `ts_id` 单调 ID 预占，`erase_by_tsid` 精确删除 | ✅ 根治，实现优于 Python 修复方案 |
| P2-F2 | `logs.info(f"...({code})")` 验证码明文写入 Logs 表 | 成功日志仅 `"验证码发送成功"` | ✅ 已修复 |
| P2-F3 | `random.randint` 弱随机源 | `std::random_device`（P3-4 模偏差亦已修复） | ✅ 已修复 |
| P3-F4 | `==` 比较存在时序侧信道 | `const_time_eq` XOR 累积比较 | ✅ 已修复 |
| P3-F5 | 邮箱仅校验 `"@" in email` | 严格正则 `^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$` | ✅ 已修复（send_email 路径） |
| P3-F6 | subject/content 无长度限制 | 256B / 4KB 上限 | ✅ 已修复 |
| P3-F7 | 发送失败响应原样回传 SMTP 异常细节 | 通用文案返回客户端，细节仅入日志 | ✅ 已修复 |

**结论**: 重写方案的"一并根除 7 项审计遗留"目标全部达成。本轮新发现的 P0/P1 均集中在重写**新增**的 `SmtpClient` 组件，与上述修复项无交集。

---

## 八、与 gateway 的协议兼容性核对

对照 `gateway/src/main.cpp` 的调用点（L525-540 verify、L2064-2085 注册发码、L2177-2195 重置发码）逐项核对：

| 核对项 | gateway 期望 | smtp3 实际 | 结论 |
|--------|-------------|-----------|------|
| 传输/封装 | pymsg TCP :8008，响应 `{success, data}` | libmsg `RpcServer` 自动封装 | ✅ |
| `verify_email` 字段 | `email` / `code`，读 `data.valid` | 一致，另兼容旧字段 `input_captcha` | ✅ |
| `send_email` 字段 | `email`（+ 可选 `subject`/`content`） | 一致 | ✅ |
| 冷却拒绝 | 读 `retry_after`（int64）与 `error` 文案透传客户端 | 一致 | ✅ |
| 发码超时 | 20s（`_TIMEOUT_SMTP_SEND`） | 内部 SMTP 会话 20s 超时，边界相当 | ✅ |
| `health` | `{status, service}` | `{"status":"ok","service":"smtp"}` | ✅ |

**有意的行为差异**（均为改进，不影响 gateway）:
1. 限流（双窗口/熔断）拒绝时 smtp3 会**回滚** 60s 冷却预占，Python 版不回滚——smtp3 语义更合理（冷却只应锚定真实发送）；
2. `subject`/`content` 为非字符串类型时 smtp3 回退默认值，Python 版 `str()` 强转（如 `"None"`）——smtp3 更稳健；
3. email 做 strip 与否：Python `strip()`、smtp3 不 strip——带首尾空格的邮箱两侧均无法通过后续正则/存储匹配，无实际影响。

---

## 九、继承自 Python 版 / 架构层面的已知项（不计入本轮发现）

- **pymsg 无认证**: 与所有 pymsg 服务同源（smtp P0-2、captcha、db 同），依赖 127.0.0.1 绑定隔离，待 `SECURITY_HARDENING_PLAN_20260803.md` 内网通信加固统一解决。
- **验证码存进程内存**: 重启即失效，单实例不可水平扩展——与 Python 版完全一致，属设计取舍。
- **SMTP 凭据明文**: 存于 `confs/configs/smtp.ini` 并经明文 pymsg 分发，与全项目 confs 模式一致。

---

## 十、修复路线图

### 立即（上线前置条件）
1. ✅ **P0-1** 重写 `read_response` 为按完整行解析（最后完整行 `^\d{3} ` 判定），并加 64KB 缓冲上限（顺带修复 P2-1）——2026-08-09 完成；
2. ✅ **P0-2** `base64_encode` 三处移位改经 `uint8_t` 转无符号，补 UTF-8 编码往返单测——2026-08-09 完成；
3. 用真实 SMTP 账号完成一次**端到端发信验证**（含中文主题/正文解码核对、残缺包/多行响应/超时路径测试）——本次审计证明"编译通过 + 绑定端口"级别的验证不足以覆盖网络协议栈重写。**待凭据就绪后执行**。

### 短期
4. ✅ **P1-2** `main()` 忽略 SIGPIPE——2026-08-09 完成；
5. **P1-1** TLS 证书链 + 主机名校验 + SNI + TLS≥1.2（**待修**）。

### 中期
6. ✅ P3-1 ~ P3-5 全部完成（2026-08-09）；逐条 INSERT 改批量仍留待 LogC 统一重构（db2/captcha2 同源）。

### 上线切换（全部 P0/P1 关闭后）
7. `confs/configs/smtp.ini` 填充真实凭据 → `core/init.txt` 切换 `smtp3/_run.sh` → `_inst.sh` 增加 `smtp3` → 观察 24h 日志后下线 Python 版。

---

## 十一、P0 修复验证用例清单（2026-08-09，14/14 通过）

验证程序从修复后的 `main.cpp` 原样提取 `base64_encode` 与 `read_response` 行判定逻辑，`g++ -std=c++20 -Wall -Wextra -O2` 编译执行，用后即删；base64 基准由 Python `base64.b64encode` 计算。

| # | 用例 | 预期 | 结果 |
|---|------|------|------|
| 1 | base64 纯 ASCII `"Hello"` | `SGVsbG8=` | ✅ |
| 2 | base64 中文主题（生产默认值 `"DNFS 邮箱验证"`） | 与 Python 基准逐字节一致 | ✅ |
| 3 | base64 中文正文（验证码占位替换后形态） | 与 Python 基准逐字节一致 | ✅ |
| 4 | base64 全高位字节 `\xE9\xE9\xE9` | `6enp` | ✅ |
| 5 | base64 长度 1/2 填充边界 | `QQ==` / `QUI=` | ✅ |
| 6 | 单行完整响应 `"220 OK\r\n"` | 解析返回 | ✅ |
| 7 | 多行完整 EHLO 响应（含 SIZE/AUTH 续行） | 解析返回 | ✅ |
| 8 | 残缺包（无尾 CRLF） | 继续读 | ✅ |
| 9 | 残缺包（多行尾行未闭合） | 继续读 | ✅ |
| 10 | 续行结尾 `"250-AUTH\r\n"` | 继续读 | ✅ |
| 11 | 响应码不匹配（550 vs 预期 250） | 异常路径 | ✅ |
| 12 | 畸形行 `"ABC X\r\n"` | 继续读，不抛 stoi | ✅ |
| 13 | 空缓冲 | 继续读 | ✅ |
| 14 | 仅 CRLF | 继续读 | ✅ |

配套验证：`bash _set.sh` 零警告重建 `smtp3.bin`（438KB），`--help` 正常响应。

---

## 附：实证记录

- 复现程序从 `main.cpp` 原样提取 `base64_encode`（L77-94）与 `read_response` 行判定逻辑（L567-599），`g++ -std=c++20 -O2` 编译执行（2026-08-09）；
- base64 基准值由 `python3 -c "base64.b64encode(...)"` 计算；
- 复现文件用后即删，未入库；结论可通过上述代码段独立复核。
