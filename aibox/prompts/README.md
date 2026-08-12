# prompts 模板（v2.9 编排者-执行者分层）

本目录是配套 v2.9 工具分层的**运行期提示词模板**，不随服务自动加载。
运行时提示词位于 `~/.aibox/prompts/`，需手工覆盖同名文件后用 `/reload_prompts` 生效：

```bash
cp aibox/prompts/{3_file_operations,4_web_access,6_command_execution,7_extension_skills,10_task_handling,99_SOUL}.md ~/.aibox/prompts/
```

> 注意：`99_SOUL.md` 可能已被 AI 通过 `update_soul` 自行更新过，覆盖前请先 diff 合并，
> 保留用户个性化内容，仅把"执行者"相关表述改为"编排者"。

变更要点：主对话不再持有命令行/文件/网页工具，执行类任务一律经 `create_subagent` 派生子代理。
