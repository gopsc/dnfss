# robot_agent — 基于 libhttp 的 DeepSeek 视觉机器人 Agent

C++20 机器人智能体骨架：命令行输入任务，调用 DeepSeek 视觉大模型（`deepseek-v4-flash-vision-exp`），通过**工具循环**驱动摄像头识图、状态查询、命令行执行与文件读写，后续可扩展硬件控制。

## 架构

```
用户任务（CLI 参数或 --prompt-file 文件）
  └─ 主代理（run_chat_loop 工具循环）
       ├─ capture_image     拍照识图（真实摄像头 / 模拟图片）
       ├─ get_robot_status  查询机器人状态
       └─ spawn_subagent    委派任务给子代理
            └─ 子代理（技能超集，独立工具循环）
                 ├─ run_command  执行 shell 命令（超时 + 输出截断）
                 ├─ read_file / write_file
                 ├─ capture_image / get_robot_status
                 └─ 返回结论 → 主代理综合回答
```

- **工具循环**：模型返回 `tool_calls` → agent 执行工具 → 结果回传 → 继续，直到模型给出最终回答（`--max-turns` 限轮）。
- **图片注入**：OpenAI 协议的 tool 消息只允许文本，工具返回的图片由主循环作为 `image_url` 注入下一轮。
- **时间戳**：会话开始时向 system prompt 注入一次当前时间（会话内不变）；每个工具结果附 `工具执行时间`，供模型判断数据新旧。
- **摄像头**：V4L2 直采，优先 MJPEG（硬件编码零转码，最快），回退 YUYV + libjpeg 编码；设备常开复用；`/dev/video*` 自动探测。

## 依赖与构建

- g++（C++20）、libhttp（`libus_httpclient.so`）、Boost.JSON、libcurl、OpenSSL、libjpeg、pthread
- libhttp 需先编译：`cd ../libhttp && make`

```bash
make          # 生成 ./robot_agent
make clean
```

## 使用

```bash
export DEEPSEEK_API_KEY=sk-xxx   # 或用 --key

./robot_agent "看看环境里有什么"                    # 真实摄像头识图
./robot_agent "报告状态"                            # 状态查询
./robot_agent "列出当前目录文件"                     # 自动委派子代理执行命令
./robot_agent --prompt-file /tmp/task.txt          # 从文件读取任务
./robot_agent "描述图片" --camera ./test.png        # 模拟摄像头（读文件，便于离线测试）
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `--prompt-file <文件>` | 从文件读取任务（与位置参数互斥） | — |
| `--camera <文件>` | 模拟摄像头：拍照工具读该图片 | 空 = 真实摄像头 |
| `--camera-device <设备>` | 摄像头设备 | 自动探测 `/dev/video*` |
| `--model <名称>` | 模型名 | `deepseek-v4-flash-vision-exp` |
| `--key <API_KEY>` | API key | 环境变量 `DEEPSEEK_API_KEY` |
| `--endpoint <URL>` | 接口地址 | `https://api.deepseek.com/chat/completions` |
| `--max-turns <N>` | 最大工具轮次 | 8 |

工具日志输出到 stderr（`[主代理][工具]` / `[子代理][工具]`），stdout 只输出最终回答，便于脚本管道。

## 代码结构

| 文件 | 职责 |
|---|---|
| `main.cpp` | CLI 解析、主代理工具注册 |
| `agent.hpp/cpp` | `run_chat_loop` 工具循环（主/子代理共用）、消息构造、时间戳注入 |
| `subagent.hpp/cpp` | 子代理：技能超集 + 独立提示词 |
| `tools.hpp/cpp` | `Tool`/`ToolRegistry` 与全部工具实现 |
| `camera.hpp/cpp` | V4L2 抓帧（MJPEG/YUYV、mmap、超时保护） |

## 扩展

- **加工具**：实现 `Tool{name, description, parameters(JSON Schema), execute}` 后 `registry.add(...)`。
- **工具返回图片**：`ToolResult.image_data_url` 填 data URL，主循环自动注入下一轮。
- **加子代理类型**：仿照 `subagent.cpp` 组装不同工具集与提示词。
- **接真实硬件**：把 `capture_image`/`get_robot_status` 的实现换成设备驱动调用即可，接口不变。

## 安全注意

`run_command` 会执行任意 shell 命令且无白名单/审批，仅建议在可信环境（你自己的机器人主机）运行；后续如需对外暴露，应先加命令审批或沙箱。
