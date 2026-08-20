# captcha2 — C++20 验证码服务

captcha 的 C++20 重实现，接口与 Python 版完全兼容（pymsg 路由 image/verify/health），修复审计发现的安全项。基于 libmsg + boost::json + FreeType。

## 架构

```
ca :8443 (GET /captcha/image 代理给浏览器)
   │  pymsg TCP
   ▼
captcha2 :8009 (RpcServer)
```

- **服务框架**: libmsg `RpcServer`（pymsg 协议，4B 大端长度前缀 + UTF-8 JSON）
- **图片生成**: FreeType 渲染旋转字符 + 干扰线 + 噪点，stb_image_write 输出 PNG
- **安全**: `std::mt19937` + `random_device` 安全随机、答案 XOR 防时序比较、全局生成速率限制
- **异步日志**: libmsg `AsyncQueue` → db2 Logs 表

## 接口

| pymsg 路由 | 说明 |
|-----------|------|
| `image` | 生成验证码，返回 `token` + base64 PNG |
| `verify` | 校验，一次性消费；错误答案 `{valid:false}`、无效 Token 带 `error` |
| `health` | 健康检查 |

请求/响应 JSON 格式与 Python captcha 完全一致，ca 零改动。

## 配置

纯 CLI + 环境变量 + 默认值，不依赖 confs。

| 配置项 | CLI 参数 | 默认值 |
|--------|---------|--------|
| 绑定地址 | `--addr` / `-a` | 127.0.0.1 |
| 监听端口 | `--port` / `-p` | 8009 |
| 工作线程 | `--workers` / `-w` | 4 |
| 验证码长度 | `--len` | 4（2~8） |
| 有效期 | `--ttl` | 300s |
| 字体路径 | `--font-path` | /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf |
| 最大存储 | `--max-store` | 10000 |
| 速率限制 | `--rate-limit` | 600 次/分钟 |
| 实例名 / 日志级别 | `--instance` / `--log-level` | captcha2 / info |

优先级: CLI > 环境变量 > 默认值；生产参数由 `core/services.yaml` 的 captcha2 条目 args 传入。

## 构建与运行

```bash
./_set.sh    # 编译
./_run.sh    # 运行
```

依赖: libmsg, boost::json, libfreetype, stb_image_write（单头文件，仓库内）

## 状态

- 生产运行中（services.yaml 拉起），Python captcha 已废弃
- 安全审计见 [docs/security/captcha2_SECURITY_AUDIT_20260809.md](../docs/security/captcha2_SECURITY_AUDIT_20260809.md)

## 许可证

MIT License
