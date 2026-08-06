# captcha 模块安全审计报告

**审计日期**: 2026-08-01  
**审计范围**: `captcha_service.py`、`log_client.py`  
**代码版本**: dev_qing 分支

---

## 代码验证记录

**验证日期**: 2026-08-03  
**验证方式**: 逐条对照审计报告与当前代码（`captcha_service.py`、`log_client.py`、`_run.sh`）

### 验证结果汇总

| 等级 | 总数 | 已修复 | 部分修复 | 未修复 | 修复率 |
|------|------|--------|----------|--------|--------|
| P0 | 0 | 0 | 0 | 0 | - |
| P1 | 1 | 1 | 0 | 0 | 100% |
| P2 | 1 | 0 | 0 | 1 | 0% |
| **合计** | **2** | **1** | **0** | **1** | **50%** |

### 逐条验证

| 编号 | 状态 | 代码位置 | 验证说明 |
|------|------|----------|----------|
| P1-1 | ✅ 已修复 | `captcha_service.py` L227-232 | 新增 `MAX_STORE_SIZE`（默认 10000，可配置 `max_store`/`CAPTCHA_MAX_STORE`），`_handle_image` 在加锁后先清理过期项，再检查容量，超限拒绝生成并返回错误，防止内存耗尽 |
| P2-1 | ❌ 未修复 | `captcha_service.py` L128 | 仍为进程内存 dict，未迁移至 Redis；当前单实例可接受 |

---

## 修复进度

| 优先级 | 总数 | 已修复 | 待修复 |
|--------|------|--------|--------|
| P0 | 0 | 0 | 0 |
| P1 | 1 | 1 | 0 |
| P2 | 1 | 0 | 1 |

---

## 🟠 P1 — 高危问题

| # | 问题 | 说明 |
|---|------|------|
| P1-1 | ~~验证码存储无容量限制，大量生成可耗尽内存~~ | ✅ 已修复（2026-08-04）：新增 `MAX_STORE_SIZE`（默认 10000），`_handle_image` 清理过期后容量仍满则拒绝生成并返回 `too many pending captchas`，防止内存耗尽 |

---

## 🟡 P2 — 中危问题

| # | 问题 |
|---|------|
| P2-1 | 内存存储不支持多实例水平扩展（需换 Redis），当前单实例可接受 |

---

## ✅ 已有安全机制

1. ✅ **一次性使用**: verify 后立即 `pop` 删除 Token（`captcha_service.py` L231）
2. ✅ **5 分钟过期**: 惰性清理（`captcha_service.py` L213-215）
3. ✅ **常数时间比较**: XOR 防时序攻击（`captcha_service.py` L239-245）
4. ✅ **仅监听 127.0.0.1**: 不对外暴露（`captcha_service.py` L181）
5. ✅ **gateway 层代理**: `Cache-Control: no-store` 禁止缓存验证码图片
6. ✅ **依赖 pymsg 协议**: recv_timeout 防 Slowloris
7. ✅ **图形验证码使用 PIL 生成**: 不依赖外部服务
8. ✅ **两层配置加载**: confs 拉取 `captcha.ini` + 环境变量覆盖（`captcha_service.py` L46-113）
9. ✅ **配置白名单合并**: `_merge_ini` 仅覆盖已知 section/key，防止意外字段注入（`captcha_service.py` L51-65）
10. ✅ **配置完整性校验**: 缺失必填项时启动失败（`captcha_service.py` L103-112）
11. ✅ **confs retry_on_refused**: confs 未就绪时自动阻塞重试（`captcha_service.py` L76-77）
12. ✅ **Token 使用 secrets.token_hex**: 加密安全随机（`captcha_service.py` L207）
13. ✅ **log_client 异步批量 + 队列上限**: `QUEUE_MAXSIZE=10000`，队列满时丢弃最旧条目（`log_client.py` L38）
14. ✅ **log_client 异常安全网**: `_on_error` 捕获 handler 外异常，线程不崩溃（`log_client.py` L150-153）

---

## 代码审查发现

### NEW-1: 验证码答案使用 random 模块生成（P2）

- **位置**: `captcha_service.py` L146
- **问题**: `generate_text()` 使用 `random.choices` 而非 `secrets` 模块生成验证码答案。`random` 模块是确定性伪随机，若攻击者能推测随机种子状态，可预测验证码答案
- **影响**: 低（Token 使用 `secrets.token_hex` 保护，攻击者需先获取有效 Token）
- **建议**: 将 `random.choices` 替换为 `secrets.SystemRandom().choices` 或逐个调用 `secrets.choice`

### NEW-2: 验证码图片生成无速率限制（P1-1 补充）

- **位置**: `captcha_service.py` L204-222
- **问题**: `_handle_image` 无任何速率限制，且 PIL 图片生成是 CPU 密集型操作，高频调用不仅消耗内存，还消耗 CPU
- **建议**: 添加滑动窗口限流（如单 IP 每分钟最多 10 次），与 smtp 模块对齐

---

## 修复路线图

### 短期
- [x] 添加验证码存储容量上限（默认 10000，超出时拒绝新生成）✅ 已修复（2026-08-04）
- [ ] 添加生成速率限制（单 IP 每分钟最多生成 N 个）
- [ ] 将 `generate_text()` 中的 `random.choices` 替换为 `secrets` 模块

### 中期
- [ ] 考虑替换为 Redis 存储支持水平扩展（当前单实例足够）
