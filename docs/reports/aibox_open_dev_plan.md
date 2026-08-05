# aibox 开放版开发计划

## 背景

当前 aibox 主分支完整功能包含**命令执行工具**（`run_command`），该功能存在 **P0-1 严重命令注入漏洞**（[aibox_SECURITY_AUDIT_20260801.md](../security/aibox_SECURITY_AUDIT_20260801.md)）：

- 使用 `shell=True` + 用户输入拼接，可执行任意系统命令
- P0-2 WebSocket 无认证，结合可导致远程 RCE
- 不适合在公开网络环境下开放使用

**目标**：创建一个**阉割版 aibox**，移除命令执行、文件操作等危险功能，保留核心对话、技能系统、记忆管理、任务调度，可安全开放使用。

## 版本分支

- `main`：保持现有完整功能，供本地可信环境使用
- `open`：开放阉割版，移除所有危险功能，安全可公开访问

## 功能裁剪方案

| 功能 | 主分支完整版 | 开放阉割版 | 备注 |
|------|-------------|------------|------|
| AI 对话（流式输出） | ✅ | ✅ | 保留核心能力 |
| WebSocket 前端交互 | ✅ | ✅ | 需要认证 |
| DeepSeek API 调用 | ✅ | ✅ | 保留 |
| 思考模式（Reasoning） | ✅ | ✅ | 保留 |
| 子代理 | ✅ | ✅ | 保留（无工具调用） |
| 情绪表达（情绪化技能） | ✅ | ✅ | 保留 |
| 记忆搜索/保存 | ✅ | ✅ | 保留 |
| 备忘录/任务调度 | ✅ | ✅ | 保留 |
| 扩展技能系统 | ✅ | ✅ | 只保留 Markdown 技能，移除可执行技能 |
| 提示词系统 | ✅ | ✅ | 保留（identity/soul 更新） |
| 对话压缩 | ✅ | ✅ | 保留 |
| SOCKS5 代理 | ✅ | ✅ | 保留（API/网页访问） |
| --- | --- | --- | --- |
| `read_file` 读文件 | ✅ | ❌ | 移除，防止任意文件读取（路径遍历 P1-1） |
| `write_file` 写文件 | ✅ | ❌ | 移除，防止任意文件写入 |
| `list_files` 列目录 | ✅ | ❌ | 移除 |
| `run_command` 命令执行 | ✅ | ❌ | **完全移除**，P0-1 根源 |
| `simple_web` 网页访问 | ✅ | ✅ | 需要 SSRF 防护（内网 IP 过滤） |
| `create_skill` 创建技能 | ✅ | ❌ | 移除（会创建文件） |
| `install_skill` 安装技能 | ✅ | ❌ | 移除（会安装可执行） |
| `update_identity` / `update_soul` | ✅ | ✅ | 保留，仅修改提示词文件 |
| --- | --- | --- | --- |

## 需要添加的安全措施

### 1. WebSocket 认证（P0-2 修复）
- 绑定地址强制改为 `127.0.0.1`，通过 gateway/WAF 反向代理接入
- 握手阶段校验 `token` 查询参数或 `Authorization: Bearer <token>`
- token 从配置文件读取，环境变量可覆盖
- 不匹配立即关闭连接，不处理任何消息

### 2. SSRF 防护（P1-2 修复）
- `SimpleWebCrawlerTool` 访问网页前解析主机 IP
- 禁止访问内网 IP 段：
  - `127.0.0.0/8`（回环）
  - `10.0.0.0/8`（私网）
  - `172.16.0.0/12`（私网）
  - `192.168.0.0/16`（私网）
  - `169.254.0.0/16`（链路本地）
  - `0.0.0.0/8`
  - `100.64.0.0/10`（共享地址）
- 禁止非 HTTP/HTTPS 协议（仅允许 `http://`/`https://`）
- 跟随重定向时重新校验目标地址

### 3. 可执行技能完全禁止
- `SkillManager` 只加载 `.md` Markdown 技能
- 跳过目录中所有可执行文件（无扩展名或 `.py`/`.sh`）
- `search_skills` 只返回 Markdown 技能
- 移除 `create_skill`/`install_skill` 工具

### 4. 文件操作限制
- **完全移除** `read_file`/`write_file`/`list_files`
- `update_identity`/`update_soul` 只允许修改 `~/.aibox/prompts/` 下固定文件，不允许路径遍历
- `check_path_safety` 严格校验文件名，确保在允许目录内

### 5. 频率限制（P2-1 修复）
- 每个连接/每个 token 每分钟限制消息轮数（默认 30 轮/分钟）
- 超限返回 `error: rate limit exceeded`

### 6. 其他加固
- 启动时强制校验 `DEEPSEEK_API_KEY` 配置，无效直接退出（P1-4 修复）
- 错误信息脱敏，不泄露路径/栈追踪（P1-3 修复）
- 日志脱敏，不记录 API Key、token 等敏感信息（P2-6 修复）

## 代码重构步骤

### 第一步：工具层移除危险工具
1. 在 `DeepSeekChat._register_tools()` 中：
   - 删除/注释 `ReadFileTool`
   - 删除/注释 `WriteFileTool`
   - 删除/注释 `ListFilesTool`
   - 删除/注释 `CommandLineTool`
   - 删除/注释 `CreateSkillTool`
   - 删除/注释 `InstallSkillTool`
   - 保留：`SearchSkillsTool`、`ReloadSkillsTool`、`AskUserTool`、`ExpressEmotionTool`、`UpdateIdentityTool`、`UpdateSoulTool`、`SaveMemoryAndEndConversationTool`、`MemoTool`、`MemoryTool`、`SimpleWebCrawlerTool`、`GetCurrentTimeTool`、`CalculatorTool`、`SubAgentTool`、`SetProxyTool`、`GetProxyTool`

### 第二步：修改 SkillManager 只加载 Markdown 技能
1. `SkillManager._load_skills()` 中只加载 `.md` 文件
2. 跳过所有其他扩展名（包括无扩展名）
3. `search_skills` 只返回 Markdown 技能
4. 移除执行相关代码路径

### 第三步：添加 WebSocket 认证
1. 在 `handle_connection` 握手时检查 token：
   - 从 `query` 中获取 `token` 参数，或从 `Authorization: Bearer` 获取
   - 与配置文件中 `auth.token` 比对
   - 不匹配：关闭连接，返回 1008 错误
2. 配置新增 `auth.token` 项，必填
3. 默认监听地址改为 `127.0.0.1`（原 `localhost`）

### 第四步：SimpleWebCrawler 添加 SSRF 防护
1. 新增 `_is_private_ip(ip)` 函数判断是否内网
2. 爬取前：
   - 解析 URL 获取主机名
   - DNS 解析得到 IP 地址列表
   - 逐个检查，任一命中内网则拒绝
   - 检查协议，只允许 http/https
3. 跟随重定向时重复检查

### 第五步：频率限制实现
1. `WebSocketHandler` 维护 `rate_limit: Dict[connection_id, (last_time, count)]`
2. 每次处理消息前检查计数，超限拒绝

### 第六步：update_identity/update_soul 路径加固
1. 只允许写 `00_IDENTITY.md` 和 `99_SOUL.md`
2. 文件名硬编码，不允许用户指定路径
3. 写入前 `realpath` 校验，确保在 `prompts_dir` 内

### 第七步：其他细节
1. 启动时强制校验 API Key，不存在立即退出
2. 错误信息脱敏，服务端记录详细错误，客户端只返回通用错误
3. 日志脱敏，不打印 API Key/token

## 配置文件变化（`config.json`）

```json
{
  // ... 原有配置不变 ...
  "auth": {
    "token": "your-secret-token-here",  // 新增 必填
    "enabled": true                     // 新增 默认 true
  },
  "rate_limit": {
    "enabled": true,                    // 新增 默认 true
    "max_requests_per_minute": 30      // 新增 默认 30
  }
}
```

## 部署说明

开放版必须通过 gateway/WAF 反向代理接入：
```
Client → WAF (TLS 终止) → gateway (认证) → aibox (127.0.0.1:8765)
```
- aibox 只监听 `127.0.0.1`，不直接暴露
- gateway 完成 token/JWT 认证
- WAF 处理 TLS 和规则过滤

## 安全验证点

发布前必须验证：

- [ ] `run_command` 工具不存在，无法调用
- [ ] `read_file`/`write_file`/`list_files` 不存在
- [ ] `create_skill`/`install_skill` 不存在
- [ ] 无 token 连接被拒绝
- [ ] 错误 token 连接被拒绝
- [ ] 正确 token 可连接
- [ ] 内网 URL 爬取被拒绝（SSRF 防护）
- [ ] 超过频率限制被拒绝
- [ ] 启动时无 API Key 直接退出
- [ ] `update_identity`/`update_soul` 只能写固定文件，无法遍历
- [ ] 只有 `.md` 技能被加载

## 发布路线图

| 阶段 | 任务 | 状态 |
|------|------|------|
| 阶段 1 | 工具裁剪、SkillManager 修改 | ⏸ 待开始 |
| 阶段 2 | WebSocket 认证实现 | ⏸ 待开始 |
| 阶段 3 | SSRF 防护（SimpleWebCrawler） | ⏸ 待开始 |
| 阶段 4 | 频率限制、启动校验、错误脱敏 | ⏸ 待开始 |
| 阶段 5 | 路径加固（update_identity/update_soul） | ⏸ 待开始 |
| 阶段 6 | 安全验证测试 | ⏸ 待开始 |
| 阶段 7 | 更新 README、更新文档 | ⏸ 待开始 |

## 风险与约束

- 阉割后功能减少，但安全性大大提高，可公开部署使用
- 仍保留 AI 对话、记忆、任务调度、子代理、扩展 Markdown 技能等核心特性
- 用户仍可通过 AI 能力总结、分析、创作，只是无法直接操作服务器文件和执行命令
- 若需要文件操作，建议通过 gateway 提供受限 API，不开放给 aibox 直接操作