# prompts 提示词模板目录

本目录是所有提示词的**唯一模板源**。`PromptManager` 启动时若 `~/.aibox/prompts/` 中缺少文件，
会自动从这里复制过去；已存在的运行时文件不会被覆盖。

## 文件清单

| 文件 | 用途 | 更新方式 |
|------|------|---------|
| `00_IDENTITY.md` | 用户身份信息 | AI 通过 `update_identity` 更新 |
| `1_memory_search.md` | 记忆搜索习惯 | 手动编辑 |
| `2_memory_save.md` | 记忆保存习惯 | 手动编辑 |
| `3_file_operations.md` | 文件操作（编排者模式） | 手动编辑 |
| `4_web_access.md` | 网页访问（编排者模式） | 手动编辑 |
| `5_memo_management.md` | 任务管理能力 | 手动编辑 |
| `6_command_execution.md` | 命令执行（编排者模式） | 手动编辑 |
| `7_extension_skills.md` | 扩展技能能力 | 手动编辑 |
| `8_identity_management.md` | 身份信息管理 | 手动编辑 |
| `9_language_instruction.md` | 语言说明与提问行为 | 手动编辑 |
| `10_task_handling.md` | 任务处理规则 | 手动编辑 |
| `11_emotion_management.md` | 情绪管理能力 | 手动编辑 |
| `99_SOUL.md` | AI 灵魂定义（角色/性格/能力边界） | AI 通过 `update_soul` 更新 |
| `100_STATUS.md` | AI 状态记录 | 对话结束时自动更新 |

## 加载顺序

`00_IDENTITY` → `1~11_技能文件`（按数字升序）→ `99_SOUL` → `100_STATUS`

## 手动更新运行时提示词

修改本目录模板后，需同步到运行目录并重载：

```bash
cp aibox/prompts/*.md ~/.aibox/prompts/
```

然后在 WebSocket 会话中执行 `/reload_prompts` 生效。

> 注意：`99_SOUL.md` 和 `00_IDENTITY.md` 可能已被 AI 自行更新过，覆盖前请先 diff 合并。
