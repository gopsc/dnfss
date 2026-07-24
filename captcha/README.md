# CAPTCHA 验证码服务

一个轻量级的验证码图片服务，仅依赖 Python 标准库实现 HTTP 服务（无需 Flask），配合 Pillow 生成验证码图片。提供图片生成、Token 校验、健康检查等接口，适合作为后端服务的验证码组件。

## 功能特性

- **图片验证码生成**：5 位字符（字母 + 数字），自动排除易混淆字符（`l I 1 O 0 o`）
- **Token 机制**：每次生成图片返回唯一 Token，校验时一次性消费，防止重放
- **过期自动清理**：Token 默认 300 秒（5 分钟）失效，每次请求时顺带清理过期项
- **线程安全**：使用锁保护内存存储，支持并发请求
- **恒定时间比较**：答案校验使用逐字符异或比较，避免计时攻击
- **干扰元素**：随机字符颜色、位置偏移、干扰线与噪点，提升识别难度
- **零框架依赖**：HTTP 层基于 `http.server`，除 Pillow 外无第三方依赖

## 技术栈

- Python 3
- [Pillow](https://python-pillow.org/)（PIL）—— 图片绘制
- Python 标准库：`http.server`、`threading`、`secrets`、`json`、`urllib`

## 安装

```bash
# 安装图片处理依赖
pip install Pillow
```

> 字体默认使用 `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`（Linux 常见路径）。若系统中不存在该字体，会自动回退到 PIL 默认字体。

## 运行

```bash
# 方式一：通过启动脚本
./_run.sh

# 方式二：直接运行
python3 captcha_service.py
```

服务启动后监听 `http://127.0.0.1:8009`。

## 配置项

在 `captcha_service.py` 顶部可调整以下常量：

| 常量          | 默认值       | 说明                  |
| ------------- | ------------ | --------------------- |
| `CAPTCHA_LEN` | `5`          | 验证码字符长度        |
| `CAPTCHA_TTL` | `300`        | Token 有效期（秒）    |
| `ADDR`        | `127.0.0.1`  | 监听地址              |
| `PORT`        | `8009`       | 监听端口              |

## API 接口

### 1. 获取验证码图片

```
GET /captcha/image
```

返回一张 PNG 验证码图片，响应头 `X-Captcha-Token` 携带本次验证的 Token，需在后续校验时一并提供。

**示例：**

```bash
curl -D - http://127.0.0.1:8009/captcha/image -o captcha.png
```

响应头示例：

```
Content-Type: image/png
X-Captcha-Token: 3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c
```

### 2. 校验验证码

```
POST /captcha/verify
Content-Type: application/json
```

请求体：

```json
{
  "token": "3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c",
  "answer": "AB3XY"
}
```

响应（校验不区分大小写，Token 校验后即失效）：

```json
{ "valid": true }
```

错误情况：

| HTTP 状态码 | 响应体                                              | 说明                    |
| ----------- | --------------------------------------------------- | ----------------------- |
| `400`       | `{ "valid": false, "error": "invalid json" }`       | 请求体非合法 JSON       |
| `400`       | `{ "valid": false, "error": "token and answer required" }` | 缺少 token 或 answer    |
| `400`       | `{ "valid": false, "error": "invalid or expired token" }`  | Token 无效或已过期      |

### 3. 健康检查

```
GET /health
```

```json
{ "status": "ok", "service": "captcha" }
```

## 典型使用流程

1. 客户端调用 `GET /captcha/image`，保存返回的图片与响应头中的 `X-Captcha-Token`。
2. 用户根据图片内容输入验证码文本。
3. 客户端调用 `POST /captcha/verify`，提交 `token` 与用户输入的 `answer`。
4. 服务端比对答案（小写化、恒定时间比较），返回 `valid` 结果，Token 随即作废。

## 项目结构

```
captcha/
├── captcha_service.py   # 服务主程序（HTTP 服务 + 验证码生成与校验）
├── _run.sh              # 启动脚本
└── README.md
```

## 注意事项

- 验证码存储在进程内存中，服务重启后所有 Token 失效，不适合多实例水平扩展场景（如需分布式可替换为 Redis 等外部存储）。
- 默认仅监听 `127.0.0.1`，仅供本机或内网调用；如需对外暴露请配合反向代理并做好限流。
- 答案校验不区分大小写，Token 一经校验即从内存中移除（无论对错）。
