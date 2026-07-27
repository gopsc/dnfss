# CAPTCHA 验证码服务

一个轻量级的图片验证码服务，仅依赖 Python 标准库实现 HTTP（无需 Flask），配合 Pillow 生成验证码图片。提供图片生成、Token 校验、健康检查三个接口，可作为后端服务的验证码组件独立部署。

服务由 `core`（PMC）的 `init.txt` 拉起，监听 `127.0.0.1:8009`，仅允许本机/内网调用；`gateway` 在登录与注册中可选地启用 CAPTCHA 校验（见 `gateway/README.md`）。

## 功能特性

- **图片验证码生成**：5 位字符（字母 + 数字），自动排除易混淆字符（`l I 1 O 0 o`）
- **Token 机制**：每次生成图片返回唯一 Token（32 位十六进制），校验时**一次性消费**，防止重放
- **过期自动清理**：Token 默认 300 秒（5 分钟）失效；`/captcha/image` 请求时顺带清理过期项，`/captcha/verify` 时若 Token 已不在存储即判为失效
- **线程安全**：使用锁（`STORE_LOCK`）保护内存存储，支持并发请求
- **恒定时间比较**：答案校验使用逐字符异或（`diff |= ord(a) ^ ord(b)`），避免计时攻击
- **干扰元素**：随机字符颜色、垂直位置偏移、干扰线与噪点，提升机器识别难度
- **零框架依赖**：HTTP 层基于标准库 `http.server`，除 Pillow 外无第三方依赖

## 技术栈

- Python 3
- [Pillow](https://python-pillow.org/)（PIL）—— 图片绘制
- Python 标准库：`http.server`、`threading`、`secrets`、`json`、`urllib`

## 安装

```bash
pip install Pillow
```

> 字体默认使用 `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`（Linux 常见路径）。若系统中不存在该字体，会自动回退到 PIL 默认字体。

## 运行

```bash
# 方式一：通过启动脚本（推荐，由 core 守护时即使用此方式）
cd captcha && ./_run.sh

# 方式二：直接运行
python3 captcha_service.py
```

服务启动后监听 `http://127.0.0.1:8009`。

## 配置项

所有可调参数均为 `captcha_service.py` 顶部的模块级常量：

| 常量          | 默认值       | 说明                              |
| ------------- | ------------ | --------------------------------- |
| `CAPTCHA_LEN` | `5`          | 验证码字符长度                    |
| `CAPTCHA_TTL` | `300`        | Token 有效期（秒）                |
| `ADDR`        | `127.0.0.1`  | 监听地址                          |
| `PORT`        | `8009`       | 监听端口                          |

## API 接口

### 接口总览

| 方法 | 路径              | 说明                                          |
| ---- | ----------------- | --------------------------------------------- |
| GET  | `/captcha/image`  | 生成验证码图片，响应头下发 `X-Captcha-Token`  |
| POST | `/captcha/verify` | 校验用户输入，一次性消费 Token                |
| GET  | `/health`         | 健康检查                                      |

---

### 1. 获取验证码图片

```
GET /captcha/image
```

生成一组新验证码（5 位字符 + 对应 Token），返回 PNG 图片二进制流，并在响应头 `X-Captcha-Token` 中携带本次校验所需的 Token。

**请求**：无参数、无请求体。

**响应头**：

| 头字段            | 取值            | 说明                              |
| ----------------- | --------------- | --------------------------------- |
| `Content-Type`    | `image/png`     | 图片格式                          |
| `Content-Length`  | 数值            | 图片字节数                        |
| `X-Captcha-Token` | 32 位十六进制串 | 本次验证码的 Token，校验时必须回传 |

**响应体**：二进制 PNG 图片，尺寸 160 × 50。

**示例**：

```bash
curl -D - http://127.0.0.1:8009/captcha/image -o captcha.png
```

响应头示例：

```
Content-Type: image/png
Content-Length: 3210
X-Captcha-Token: 3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c
```

> Token 有效期 300 秒，且**一次性使用**——无论校验成功与否，第一次 `/captcha/verify` 调用后即被消费。

---

### 2. 校验验证码

```
POST /captcha/verify
Content-Type: application/json
```

提交 `/captcha/image` 下发的 Token 与用户输入，比对答案（大小写不敏感）。**Token 在调用后被立即移除（一次性）。**

**请求体字段**：

| 字段    | 类型   | 必填 | 说明                                       |
| ------- | ------ | ---- | ------------------------------------------ |
| `token` | string | 是   | `GET /captcha/image` 响应头中的 `X-Captcha-Token` |
| `answer` | string | 是   | 用户输入的验证码文本，校验前统一转小写，大小写不敏感 |

**请求体示例**：

```json
{
  "token": "3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c",
  "answer": "AB3XY"
}
```

**响应**：

| HTTP 状态码 | 响应体                                     | 触发条件                                       |
| ----------- | ------------------------------------------ | ---------------------------------------------- |
| `200`       | `{ "valid": true }`                        | Token 有效且答案正确                           |
| `200`       | `{ "valid": false }`                       | Token 有效但**答案错误**（Token 已消费）      |
| `400`       | `{ "valid": false, "error": "invalid json" }`            | 请求体非合法 JSON                  |
| `400`       | `{ "valid": false, "error": "token and answer required" }` | 缺少 `token` 或 `answer` 字段       |
| `400`       | `{ "valid": false, "error": "invalid or expired token" }`  | Token 无效、已消费或已过期            |

> ⚠️ **注意区分**：答案错误与 Token 错误是两种不同的结果。答案错误（Token 仍有效）返回 `200 {valid:false}`；而 Token 缺失/失效/过期会直接返回 `400`。消费方（如 gateway）应据此分别处理：前者提示"验证码错误"，后者提示"请重新获取验证码"。

**示例**：

```bash
curl -X POST http://127.0.0.1:8009/captcha/verify \
  -H 'Content-Type: application/json' \
  -d '{"token":"3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c","answer":"AB3XY"}'
```

---

### 3. 健康检查

```
GET /health
```

**响应**（HTTP 200）：

```json
{ "status": "ok", "service": "captcha" }
```

---

## 数据模型与 Token 生命周期

- **存储**：进程内存字典 `captcha_store`，键为 Token，值为 `{ answer: <小写答案>, created_at: <Unix 时间戳> }`。
- **生成**：`GET /captcha/image` 时写入一条记录，并返回对应 Token。
- **消费**：`POST /captcha/verify` 时 `pop` 移除该 Token，无论答案对错，均不可再次使用。
- **过期**：超过 `CAPTCHA_TTL`（300 秒）即视为失效。`/captcha/image` 会在每次生成时顺带清理过期项；`/captcha/verify` 时若 Token 不在存储中（过期、未生成或已消费），统一返回 `400 invalid or expired token`。

## 集成关系

- 由 `core`（`PMC`）通过 `init.txt` 中的 `bash /opt/micro_servers/captcha/_run.sh` 拉起并守护。
- `gateway` 在 `/login` 与 `/register` 中可选地启用 CAPTCHA 校验（以 `captcha_token` + `captcha_answer` 字段传入，见 `gateway/README.md`）。
- 在集中式日志链路中，`captcha` 作为 `service` 名之一，可经 `log_client` SDK 推送日志至 `db` 的 `/log/ingest`。

## 典型使用流程

1. 客户端调用 `GET /captcha/image`，保存返回的图片与响应头中的 `X-Captcha-Token`。
2. 用户根据图片内容输入验证码文本。
3. 客户端调用 `POST /captcha/verify`，提交 `token` 与用户输入的 `answer`。
4. 服务端比对答案（小写化、恒定时间比较），返回 `valid` 结果；Token 随即作废（无论对错）。

## 项目结构

```
captcha/
├── captcha_service.py   # 服务主程序（HTTP 服务 + 验证码生成与校验）
├── _run.sh              # 启动脚本（cd 后 exec python3 captcha_service.py）
└── README.md
```

## 注意事项

- 验证码存储在进程内存中，服务重启后所有 Token 失效；**不支持多实例水平扩展**（如需分布式部署，应将 `captcha_store` 替换为 Redis 等外部存储，并相应调整过期清理逻辑）。
- 默认仅监听 `127.0.0.1`，仅供本机或内网调用；如需对外暴露，请配合反向代理（如 `gateway`/`waf`）并做好限流。
- 答案校验不区分大小写；Token 一经校验即从内存中移除（一次性使用）。
- 字体缺失时自动回退到 PIL 默认字体，可能影响验证码观感，部署前建议确认 DejaVu 字体已安装。
