# CAPTCHA 验证码服务

一个轻量级的图片验证码服务，基于自研 [pymsg](../pymsg/) 协议（TCP 长度前缀帧 + JSON），配合 Pillow 生成验证码图片。提供图片生成、Token 校验、健康检查三个路由，由 `gateway` 经 pymsg 调用并代理给浏览器。

服务由 `core`（PMC）的 `init.txt` 拉起，监听 `127.0.0.1:8009`（TCP），仅允许本机/内网调用。

## 功能特性

- **图片验证码生成**：4 位字符（字母 + 数字），自动排除易混淆字符（`l I 1 O 0 o`）
- **Token 机制**：每次生成图片返回唯一 Token（32 位十六进制），校验时**一次性消费**，防止重放
- **过期自动清理**：Token 默认 300 秒（5 分钟）失效；`image` 路由请求时顺带清理过期项
- **线程安全**：使用锁（`STORE_LOCK`）保护内存存储，支持并发请求
- **恒定时间比较**：答案校验使用逐字符异或（`diff |= ord(a) ^ ord(b)`），避免计时攻击
- **干扰元素**：随机字符颜色、垂直位置偏移、干扰线与噪点，提升机器识别难度
- **零框架依赖**：基于 pymsg（项目内置库），除 Pillow 外无第三方依赖

## 技术栈

- Python 3
- [Pillow](https://python-pillow.org/)（PIL）—— 图片绘制
- [pymsg](../pymsg/) —— TCP 长度前缀帧协议（`[4字节大端长度][UTF-8 JSON]`）

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
python3 captcha_service.py            # 默认 127.0.0.1:8009
python3 captcha_service.py --port 8009 --addr 127.0.0.1
```

## 协议与路由

服务基于 pymsg 协议：每条消息为完整 JSON，`_route` 字段指定路由。响应格式与 db 服务一致：

```json
{"success": true,  "data": {...}}
{"success": false, "error": "..."}
```

| _route  | 说明                                       |
| ------- | ------------------------------------------ |
| `image`   | 生成验证码图片（base64 PNG + Token）       |
| `verify`  | 校验用户输入，一次性消费 Token             |
| `health`  | 健康检查                                   |

### 1. image — 生成验证码图片

**请求**：

```json
{"_route": "image"}
```

**响应**：

```json
{
  "success": true,
  "data": {
    "token": "3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c",
    "image": "<base64 编码的 PNG，160×50>"
  }
}
```

> Token 有效期 300 秒，且**一次性使用**——无论校验成功与否，第一次 `verify` 调用后即被消费。

### 2. verify — 校验验证码

**请求**：

```json
{"_route": "verify", "token": "3f9a1c2b4d5e6f7a8b9c0d1e2f3a4b5c", "answer": "AB3XY"}
```

**响应**（均为 `success: true`，业务结果在 `data` 中区分）：

| 响应 data                                                     | 触发条件                       |
| ------------------------------------------------------------- | ------------------------------ |
| `{ "valid": true }`                                           | Token 有效且答案正确           |
| `{ "valid": false }`                                          | Token 有效但**答案错误**（Token 已消费） |
| `{ "valid": false, "error": "token and answer required" }`    | 缺少 `token` 或 `answer` 字段  |
| `{ "valid": false, "error": "invalid or expired token" }`     | Token 无效、已消费或已过期     |

> ⚠️ **注意区分**：答案错误与 Token 错误是两种不同的结果。消费方（如 gateway）应分别处理：前者提示"验证码错误"，后者提示"请重新获取验证码"。

### 3. health — 健康检查

**请求**：`{"_route": "health"}`
**响应**：`{"success": true, "data": {"status": "ok", "service": "captcha"}}`

### 调用示例（Python）

```python
import json, sys
sys.path.insert(0, "../pymsg")
from pymsg import MsgClient

c = MsgClient("127.0.0.1", 8009, "my-service")
resp = json.loads(c.call(json.dumps({"_route": "image"}), timeout=5))
token = resp["data"]["token"]
png_b64 = resp["data"]["image"]
```

## 数据模型与 Token 生命周期

- **存储**：进程内存字典 `captcha_store`，键为 Token，值为 `{ answer: <小写答案>, created_at: <Unix 时间戳> }`。
- **生成**：`image` 路由时写入一条记录，并返回对应 Token 与 base64 PNG。
- **消费**：`verify` 路由时 `pop` 移除该 Token，无论答案对错，均不可再次使用。
- **过期**：超过 `CAPTCHA_TTL`（300 秒）即视为失效。`image` 路由每次生成时顺带清理过期项；`verify` 时若 Token 不在存储中（过期、未生成或已消费），统一返回 `invalid or expired token`。

## 集成关系

- 由 `core`（`PMC`）通过 `init.txt` 中的 `bash /opt/micro_servers/captcha/_run.sh` 拉起并守护。
- `gateway` 提供 **`GET /captcha/image`** 代理路由：经 pymsg 调本服务的 `image` 路由，将 base64 PNG 解码后以 `image/png` 响应体 + `X-Captcha-Token` 响应头返回给浏览器（`Cache-Control: no-store`）。
- `gateway` 在 `/login` 与 `/register` 中可选地启用 CAPTCHA 校验（以 `captcha_token` + `captcha_answer` 字段传入，见 `gateway/README.md`）。

## 典型使用流程

1. 前端请求 `GET https://<host>/captcha/image`（经 waf → gateway → captcha），展示图片并保存 `X-Captcha-Token`。
2. 用户根据图片内容输入验证码文本。
3. 前端在登录/注册请求中携带 `captcha_token` + `captcha_answer`。
4. 服务端比对答案（小写化、恒定时间比较），Token 随即作废（无论对错）。

## 项目结构

```
captcha/
├── captcha_service.py   # 服务主程序（pymsg 服务 + 验证码生成与校验）
├── _run.sh              # 启动脚本（cd 后 exec python3 captcha_service.py）
└── README.md
```

## 注意事项

- 验证码存储在进程内存中，服务重启后所有 Token 失效；**不支持多实例水平扩展**（如需分布式部署，应将 `captcha_store` 替换为 Redis 等外部存储，并相应调整过期清理逻辑）。
- 默认仅监听 `127.0.0.1`，仅供本机或内网调用；浏览器侧图片获取统一走 `gateway` 的 `GET /captcha/image` 代理。
- 答案校验不区分大小写；Token 一经校验即从内存中移除（一次性使用）。
- 字体缺失时自动回退到 PIL 默认字体，可能影响验证码观感，部署前建议确认 DejaVu 字体已安装。
- 旧版 HTTP 接口（http.server）已下线，协议与 db 服务统一为 pymsg/TCP。
