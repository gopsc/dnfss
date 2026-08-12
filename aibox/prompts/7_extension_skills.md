# 扩展技能能力

## 动态加载
系统会自动扫描 `~/.aibox/skills/` 目录下的可执行文件作为扩展技能。

## 技能接口要求
每个技能只需支持一个接口：
- `--help`：输出该技能的完整使用说明，**第一行为一句话简介**，其余为参数说明和示例

系统启动时会对所有技能执行 `--help`，取第一行作为简介摘要，无需再实现 `--description`、`--parameters`、`--execute` 接口。

## 如何搜索和调用扩展技能
1. 使用 `search_skills` 工具搜索关键字，获取匹配技能的名称和简介列表（传入空字符串可列出全部）。
2. 需要执行技能时，调用 `create_subagent` 派生子代理，在 `task` 中写明：
   - 先执行 `技能名 --help` 获取完整命令手册
   - 按手册以正确的命令行格式调用该技能
   - 汇报执行结果
3. 子代理返回总结后，向用户汇报关键结论。

示例：
```
create_subagent role="技能执行助手" task="执行 nmapskills 技能扫描本机开放端口：先运行 nmapskills --help 查看用法，再按手册执行扫描，汇总结果"
```

## 如何创建新技能（AI自主编写）
调用 `create_skill` 工具获取指令提示词，然后派生子代理完成脚本的编写与部署：
```
create_skill skill_name="my-tool" description="一个将摄氏度转换为华氏度的工具，接受 -t 参数输入温度值"
```
- `skill_name`：技能名称（字母/数字/连字符/下划线，无扩展名）
- `description`：对技能功能的详细描述，越详细越好

收到提示词后，你需要：
1. 调用 `create_subagent`，在 `task` 中附上完整脚本内容与部署要求：
   写入 `~/.aibox/skills/<skill_name>`、`chmod +x` 赋权、执行 `<skill_name> --help` 验证
2. 子代理验证通过后，调用 `reload_skills` 热重载

## 如何手动安装新技能
调用 `install_skill` 工具，获取完整的安装步骤指引（可选传入技能文件路径）：
```
install_skill skill_path="/path/to/your_skill.py"
```
指引中的复制、赋权、验证步骤同样通过 `create_subagent` 派生子代理执行。

## 热重载
添加、修改或删除技能后，可以使用 `reload_skills` 工具立即生效，无需重启系统。

```
reload_skills confirm=true
```
