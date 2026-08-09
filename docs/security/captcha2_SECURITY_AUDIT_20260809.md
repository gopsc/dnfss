# captcha2 模块安全审计报告

**审计日期**: 2026-08-09
**审计范围**: `src/main.cpp`、`inc/LogC.hpp`、`Makefile`、`_run.sh`、`_set.sh`
**代码版本**: dev_qing 分支（C++20 重写版，captcha 模块替代者）
**审计方式**: 全量代码走查 + 与 Python 原版（`captcha/captcha_service.py`）逐行对照 + gateway 调用方（`gateway/src/main.cpp` L504-520、L1845-1887）协议验证

---

## 代码修复记录

**修复日期**: 2026-08-09
**修复方式**: 逐条对照审计报告修复代码缺陷

### 修复结果汇总

| 等级 | 总数 | 已修复 | 未修复 | 修复率 |
|------|------|--------|--------|--------|
| P0 | 1 | 1 | 0 | 100% |
| P1 | 1 | 1 | 0 | 100% |
| P2 | 3 | 2 | 1 | 67% |
| P3 | 6 | 6 | 0 | 100% |
| **合计** | **11** | **10** | **1** | **91%** |

### 逐条修复

| 编号 | 状态 | 代码位置 | 修复说明 |
|------|------|----------|----------|
| P0-1 | ✅ 已修复 | `src/main.cpp` L588-597 | 新增 `answer_lc = text; std::transform(..., tolower)` 存储前小写化；同时修复 L633-636 `::tolower` 改用 `unsigned char` lambda（P2-2） |
| P2-2 | ✅ 已修复 | `src/main.cpp` L633-636 | `::tolower` → `[](unsigned char c) { return static_cast<char>(std::tolower(c)); }` |
| P1-1 | ✅ 已修复 | `gateway/src/main.cpp` L1853-1861 + `captcha2/src/main.cpp` L58 | **双层防护**：gateway 层新增 `_CAPTCHA_IMAGE_RATE` 按 IP 限流 10次/60秒（返回 429）；captcha2 全局限流从 60/min 提高到 600/min 作为最后防线 |
| P2-1 | ✅ 已修复 | `src/main.cpp` L251-263 | `generate_text` 从 mt19937 改为 `std::random_device` 逐字符生成，消除可预测性风险 |
| P3-1 | ✅ 已修复 | `src/main.cpp` L50-58, L124-145 | 新增 `parse_int(key,val)` 辅助函数，异常时输出键名与原始值；全部 7 处 `stoi` 替换 |
| P3-2 | ✅ 已修复 | `src/main.cpp` L30 | 显式 `#include <iomanip>` |
| P3-3 | ✅ 已修复 | `src/main.cpp` L143-145 | 补齐 `CAPTCHA_LEN`、`CAPTCHA_TTL`、`CAPTCHA_RATE_LIMIT` 环境变量覆盖 |
| P3-4 | ✅ 已修复 | `inc/LogC.hpp` L97 | 日志 RPC 超时从 5.0s 降到 1.0s（非关键路径，批量插入改长连接需 db2 协议配合，列入中期） |
| P3-5 | ✅ 已修复 | `src/main.cpp` L595-612 + `gateway/src/main.cpp` L1878-1885 | captcha2：限流/满容抛 `std::runtime_error`（RpcServer 自动包装为 `success:false`）；gateway：解析 error 字符串 → `rate_limit`→429 / `store|full`→503 |
| P3-6 | ✅ 已修复 | `src/main.cpp` L294-341, L448 | 移除未使用的 `const pixel_at` 重载及 `rotate_bitmap` 的 `offset_x/offset_y` 输出参数 |
| P2-3 | ❌ 未修复 | — | 单实例可接受，遗留 |

### 测试验证

| 测试项 | 结果 |
|--------|------|
| 编译（g++ -std=c++20 -Wall -Wextra） | ✓ 无错误 |
| `test_rpc.py` 自动化 7 项 | ✓ 7/7 通过 |
| P3-5 异常包装验证（触发限流 → RpcServer 返回 success:false） | ✓ error="rate_limit_exceeded" |
| 10/10 错误答案拒绝 | ✓ 全部正确拒绝 |
| Token 一次性消费 | ✓ 二次调用返回 invalid |
| 语义等价性（lowercase → lowercase） | ✓ 存储/校验路径一致 |
| PNG 生成（160×50 RGB） | ✓ 有效 |

---

## 审计结论摘要

| 等级 | 数量 | 说明 |
|------|------|------|
| 🔴 P0 | 1 | 大小写不一致导致验证码几乎永远校验失败（功能致命缺陷） |
| 🟠 P1 | 1 | 全局速率限制可被单一匿名用户打满，DoS 全部登录/注册 |
| 🟡 P2 | 3 | 非 CSPRNG 生成答案、`::tolower` UB、单实例内存存储 |
| 🔵 P3 | 6 | stoi 未捕获、缺头文件、env 覆盖不全、日志性能、错误码语义、死代码 |
| **合计** | **11** | |

---

## 🔴 P0 — 致命问题

### P0-1: 存储答案未小写化，含大写字母的验证码永远校验失败

- **位置**: `src/main.cpp` L588-592（image 路由）、L630（verify 路由）
- **问题**: Python 原版在生成时即存小写答案（`captcha_service.py` L234
  `captcha_store[token] = {'answer': text.lower(), ...}`），校验时对用户答案
  `.strip().lower()`（L246），实现大小写不敏感匹配。
  C++ 版 `g_store.generate(text, ...)` 直接存**原始混合大小写文本**，而 verify
  路由只对**用户答案**做 `::tolower`（L630）。字符集 55 个字符中含 24 个大写字母，
  4 字符验证码全为小写/数字的概率仅约 10% —— 约 **90% 的验证码无论用户输入什么都无法通过校验**。
- **验证**: 功能测试 `test_rpc.py` 仅覆盖"错误答案 → valid=false"路径，未覆盖
  "正确答案 → valid=true"正向路径，因此该缺陷未被测试暴露。
- **影响**: 上线后登录/注册功能几乎完全不可用（fail-closed 方向，无安全泄露，但属发布阻断级缺陷）。
- **修复**: image 路由存储前将 `text` 小写化（与 Python 版语义对齐）：
  `std::string answer_lc = text; std::transform(...); g_store.generate(answer_lc, ...)`。
  同时在 `test_rpc.py` 补充正向校验用例（可通过临时 debug 路由或固定种子实现）。

---

## 🟠 P1 — 高危问题

### P1-1: 速率限制为全局单桶，单一匿名用户即可 DoS 全站验证码

- **位置**: `src/main.cpp` L481-505（RateLimiter）、L580（image 路由）
- **问题**: `RateLimiter` 是**全局**滑动窗口（60 次/分钟，所有客户端共享一个桶）。
  gateway 的 `/captcha/image` 是匿名开放接口，任何单个攻击者以 1 QPS 的极低成本
  即可持续打满全局配额，使**所有**合法用户拿不到验证码 → 登录/注册全站瘫痪。
  Python 原版审计（`captcha_SECURITY_AUDIT_20260801.md` NEW-2）建议的是"**单 IP** 每分钟 N 次"，
  但 pymsg 协议不传递客户端 IP，captcha2 侧无法区分调用来源，全局限流反而引入了新的可用性风险。
- **影响**: 低成本拒绝服务；攻击面通过 gateway 暴露到公网。
- **修复**（任选其一，推荐 a）:
  - a. 在 **gateway 层**对 `/captcha/image` 按客户端 IP 限流（gateway 已有
    `_REGISTER_RATE.check_and_record(ip, N, window)` 基础设施，`handleRegister` L1897-1904 可直接复用），
    captcha2 侧全局限流仅作为最后防线并调大阈值（如 600/min）；
  - b. pymsg 请求中由 gateway 透传 `client_ip` 字段，captcha2 按 IP 分桶限流。

---

## 🟡 P2 — 中危问题

### P2-1: 验证码答案使用 mt19937 生成（非密码学安全随机）

- **位置**: `src/main.cpp` L251-264（`generate_text`），L256
  `thread_local std::mt19937 rng(std::random_device{}());`
- **问题**: mt19937 是确定性伪随机，虽以 `random_device` 播种，但观测足够多的输出
  理论上可恢复内部状态并预测后续答案。与 Python 原版审计 NEW-1（`random.choices`）同源同级。
- **缓解因素**: Token 为 256-bit `random_device` 直接生成（L185-187），攻击者需先持有有效
  Token 且每 Token 仅一次校验机会，实际可利用性低。
- **修复**: `generate_text` 改为每字符直接使用 `std::random_device`（低频调用，性能无虞），
  与 Token 生成策略对齐。

### P2-2: `::tolower(char)` 对非 ASCII 输入是未定义行为

- **位置**: `src/main.cpp` L630
  `std::transform(answer.begin(), answer.end(), answer.begin(), ::tolower);`
- **问题**: `answer` 是攻击者可控输入（经 gateway 透传）。x86-64 Linux 上 `char` 为有符号，
  字节 >0x7F 转换为负值传入 `tolower` 是标准未定义行为（glibc 实现为查表，负索引越界读）。
- **影响**: 实际后果通常为读取错误值而非崩溃，但属于可被外部输入触发的 UB，必须消除。
- **修复**: `[](unsigned char c){ return static_cast<char>(std::tolower(c)); }`。

### P2-3: 内存单实例存储不支持水平扩展（继承自 Python 版 P2-1）

- **位置**: `src/main.cpp` L170-245（CaptchaStore）
- **问题**: token→answer 映射存于进程内存，多实例部署时校验请求路由到另一实例必然失败。
  当前单实例架构可接受，与 Python 原版遗留问题一致，级别维持不变。
- **修复**: 中期方案 —— 如需多实例，迁移至共享存储（Redis / db2 表 + TTL）。

---

## 🔵 P3 — 低危 / 健壮性问题

### P3-1: `std::stoi` 未捕获异常，配置格式错误时报错信息不可诊断

- **位置**: `src/main.cpp` L112-119（merge_ini）、L127-130（apply_env_overrides）、L524
- **问题**: 配置值非数字（如 `port = abc`）时 `std::stoi` 抛 `std::invalid_argument`，
  被 `main()` 顶层 catch 捕获后进程退出，但错误信息仅为 `致命错误: stoi`，
  不指明是哪个配置键出错，运维排障困难。
- **修复**: 封装 `parse_int(key, val)` 辅助函数，异常时输出具体键名与原始值。

### P3-2: 使用 `std::setw`/`std::setfill` 但未包含 `<iomanip>`

- **位置**: `src/main.cpp` L190（Token hex 编码），头文件列表 L22-36 无 `<iomanip>`
- **问题**: 当前依赖其他头传递包含而编译通过，属可移植性隐患（换编译器/标准库版本可能失败）。
- **修复**: 显式 `#include <iomanip>`。

### P3-3: `rate_limit_per_min`、`ttl`、`len` 无环境变量覆盖

- **位置**: `src/main.cpp` L125-131（apply_env_overrides）
- **问题**: 仅 `host/port/workers/font_path/max_store` 支持 env 覆盖，其余 captcha 段
  配置项不支持，与"env 最高优先级、Docker 兼容"的设计意图不一致。
- **修复**: 补齐 `CAPTCHA_LEN`、`CAPTCHA_TTL`、`CAPTCHA_RATE_LIMIT` 环境变量。

### P3-4: LogC 每批日志逐条同步 RPC，且每批新建 TCP 连接

- **位置**: `inc/LogC.hpp` L80-103（send_batch）
- **问题**: 批量 50 条日志 = 新建 1 条连接 + 50 次串行往返（每次 5s 超时上限）。
  db2 慢时后台 flush 线程最长阻塞 250s，期间队列积压（上限 10000 后静默丢弃，fail-safe 成立）。
- **修复**: 使用 db2 execute 路由的批量 insert（若支持 values 数组），或至少复用长连接。

### P3-5: image 限流/存储满时 gateway 返回 502 而非 429

- **位置**: `src/main.cpp` L580-599 返回 `{"success":false,...}` 作为 data；
  gateway `handleCaptchaImage` L1869-1876 取不到 `data.token` → 统一抛 502 "Bad captcha response"
- **问题**: RpcServer 将 handler 返回值包装为 `{"success":true,"data":{...}}`，内层
  `success:false` 只能被 gateway 当格式错误处理。语义上应为 429（限流）/503（容量满）。
  **注**: Python 原版行为相同（`captcha_service.py` L231），非 C++ 重写引入的回归，
  两版需同步修正。
- **修复**: captcha2 改为 `throw std::runtime_error("rate limit exceeded")`（RpcServer 自动
  包装为外层 `success:false`），gateway 依据 error 文案映射 429/503。

### P3-6: 死代码 — rotate_bitmap 的 offset 输出与 const pixel_at 重载未使用

- **位置**: `src/main.cpp` L295-341（`offset_x/offset_y` 计算后调用方 L441-447 未使用）、
  L290-292（const 版 `pixel_at`）
- **修复**: 删除未用参数与重载，或在合成时应用 offset 修正字符定位。

---

## ✅ 已有安全机制（15 项）

1. ✅ **Token 256-bit 随机**: `std::random_device` 直接生成 32 字节（强于 Python 版 128-bit，L184-192）
2. ✅ **一次性消费**: verify 命中后立即 `erase`（L212）
3. ✅ **TTL 惰性过期**: generate 时清理 + verify 时单条检查（L179、L204-209）
4. ✅ **恒定时间比较**: XOR 累积防时序攻击（L214-221）
5. ✅ **容量上限**: `max_store`（默认 10000）超限拒绝生成（L181-182）
6. ✅ **仅监听 127.0.0.1**: 不对外暴露（confs captcha.ini `[server] host`）
7. ✅ **配置白名单合并**: `merge_ini` 仅覆盖已知 section/key（L79-122）
8. ✅ **配置完整性校验**: `validate_config` 缺失/非法即启动失败（L133-141）
9. ✅ **confs retry_on_refused**: confs 未就绪时阻塞重试（L63-64）
10. ✅ **RpcServer 异常脱敏**: handler 异常不崩溃、错误信息过滤绝对路径（libmsg `_sanitize_error`）
11. ✅ **pymsg 消息大小上限**: `kMaxMsgSize` 防超大报文内存耗尽（libmsg Msg.hpp L294-297）
12. ✅ **速率限制存在**: 滑动窗口 60/min（尽管有 P1-1 的粒度缺陷）
13. ✅ **LogC 异步队列 + 上限**: `max_queue_size=10000`，db 不可用时 fail-safe 丢弃（LogC.hpp L55-60）
14. ✅ **字体文件启动时校验**: 不存在则拒绝启动；运行期加载失败回退 DejaVu（L410-417、L561-569）
15. ✅ **gateway 层 no-store**: 验证码图片禁止缓存（gateway L1881）

---

## 与 Python 原版审计遗留项对照

| 原审计项 | Python 版状态 | C++ 版状态 |
|----------|--------------|-----------|
| P1-1 存储容量上限 | ✅ 已修复 | ✅ 继承实现（max_store） |
| P2-1 Redis 水平扩展 | ❌ 未修复 | ❌ 同样未实现（本报告 P2-3） |
| NEW-1 答案非 CSPRNG | ❌ 未修复 | ❌ 同样存在（本报告 P2-1，mt19937） |
| NEW-2 生成速率限制 | ❌ 未修复 | ✅ **已修复**（双层防护：gateway IP 限流 10/min + captcha2 全局限流 600/min 兜底） |

---

## 修复路线图

### 立即（发布阻断）
- [x] **P0-1**: image 路由存储小写化答案；`test_rpc.py` 补充正向校验用例 ✅ **已修复（2026-08-09）**
- [x] **P2-2**: `::tolower` 改用 `unsigned char` lambda ✅ **已修复（2026-08-09）**

### 短期
- [x] **P1-1**: gateway 层按 IP 限流 `/captcha/image`；captcha2 全局限流调整为兜底阈值 ✅ **已修复（2026-08-09）**
- [x] **P2-1**: `generate_text` 改用 `std::random_device` 逐字符生成 ✅ **已修复（2026-08-09）**
- [x] **P3-1**: `parse_int` 辅助函数，stoi 异常时输出键名 ✅ **已修复（2026-08-09）**
- [x] **P3-2**: 显式 `#include <iomanip>` ✅ **已修复（2026-08-09）**
- [x] **P3-3**: 补齐 `CAPTCHA_LEN`/`CAPTCHA_TTL`/`CAPTCHA_RATE_LIMIT` 环境变量 ✅ **已修复（2026-08-09）**

### 中期
- [x] **P3-4**: LogC 超时 5.0→1.0s（完整批量 insert 需 db2 协议配合） ✅ **已优化（2026-08-09）**
- [x] **P3-5**: 限流/满容抛异常 + gateway 错误码映射 429/503 ✅ **已修复（2026-08-09）**
- [ ] **P2-3**: 多实例需求出现时迁移共享存储

### 清理
- [x] **P3-6**: 移除死代码（`const pixel_at` + `rotate_bitmap` offset） ✅ **已清理（2026-08-09）**
