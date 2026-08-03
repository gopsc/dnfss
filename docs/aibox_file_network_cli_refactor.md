# aibox 文件操作与网络操作命令行化 开发计划

## 目标

将 aibox 中所有 Python 原生实现的文件操作和网络操作改为通过系统命令行工具实现，统一工具执行模式，减少 Python 依赖。

## 涉及模块

| 现有类 | 作用 | 替换方案 |
|--------|------|----------|
| `FileToolBase` | 文件工具基类 | 简化为命令行工具基类 |
| `ReadFileTool` | 读文件 | `cat` / `head` / `tail` / `wc` |
| `WriteFileTool` | 写文件 | `mkdir -p` + shell 重定向 |
| `ListFilesTool` | 列目录 | `ls` / `find` / `du` |
| `SimpleWebClient` | HTTP 客户端 | `curl` |
| `WebPage` | 网页解析对象 | 移除，curl + grep/sed 替代 |
| `SimpleWebCrawlerTool` | 网页爬虫工具 | `curl` + 文本处理管道 |

## 阶段一：文件操作命令行化

### 1.1 ReadFileTool 重构

用以下命令行工具替代 Python 文件 I/O：

| 原功能 | 命令行实现 |
|--------|-----------|
| `head=True` 读取开头 N 行 | `head -n N` |
| `tail=True` 读取末尾 N 行 | `tail -n N` |
| 指定行范围 `start_line ~ end_line` | `sed -n 'start,endp'` |
| 统计行数 `_count_lines` | `wc -l` |
| 文件编码检测 | `file -b --mime-encoding` |
| 单行超长截断 | `cut -c 1-10000` |

保留的安全限制：
- 文件大小限制 `MAX_FILE_SIZE`：通过 `wc -c` 或 `stat -f%z` 检查
- 行数上限 500 行：`head -n 500` 作为最后一道防线
- 单行 10KB 限制：`cut -c 1-10000`

### 1.2 WriteFileTool 重构

| 原功能 | 命令行实现 |
|--------|-----------|
| 创建目录 | `mkdir -p` |
| 覆盖写入 `mode=w` | `content > filepath` |
| 追加写入 `mode=a` | `content >> filepath` |
| 检查文件大小 | `wc -c` 或 `stat -f%z` |
| 路径安全检查 | `realpath` / `test -f` |

### 1.3 ListFilesTool 重构

| 原功能 | 命令行实现 |
|--------|-----------|
| 列出目录内容 | `ls -la` 或 `find . -maxdepth 1` |
| 隐藏文件过滤 | `ls -la` 默认不显示 `.` 开头的隐藏文件（或 `find` 排除） |
| 文件大小统计 | `du -sh *` 或 `ls -lh` |
| 目录/文件区分 | `ls -F` 或 `find -type f/d` |

### 1.4 FileToolBase 简化

移除 `normalize_path()`、`check_path_safety()` 中 Python `os` 模块调用，改为：
- 路径规范化：`realpath`
- 文件存在性检查：`test -f` / `test -d`
- 文件大小检查：`wc -c` 或 `stat`

### 1.5 安全边界保留

- `MAX_FILE_SIZE`（默认 10MB）：通过命令检查
- 行数上限 500 行：`head -n 500` 兜底
- 单行长度上限 10KB：`cut -c 1-10000` 兜底
- 所有路径参数 `shlex.quote()` 转义

## 阶段二：网络操作命令行化

### 2.1 SimpleWebClient → curl

| 原功能 | 命令行实现 |
|--------|-----------|
| HTTP GET 请求 | `curl -sL --max-time N` |
| 自定义 User-Agent | `curl -A "..."` |
| SOCKS5 代理 | `curl --socks5 host:port` 或环境变量 `ALL_PROXY` |
| 请求间隔控制 | `SubprocessCommandExecutor` 层面控制 |
| 响应状态码 | `curl -sL -o /dev/null -w '%{http_code}'` |
| 下载大小限制 | `curl --max-filesize N` 或管道 `head -c N` |

### 2.2 WebPage 移除

`WebPage` 类不再需要，其功能由命令行管道实现：

| 原属性/方法 | 命令行实现 |
|------------|-----------|
| `title` | `curl -sL | grep -oP '<title>\K[^<]+'` |
| `text` | `curl -sL | lynx -dump -stdin` 或 `html2text` |
| `links` | `curl -sL | grep -oP 'href="[^"]*"'` |
| `status_code` | `curl -sL -o /dev/null -w '%{http_code}'` |
| `headers` | `curl -sL -I` |

### 2.3 SimpleWebCrawlerTool 重构

| action | 命令行实现 |
|--------|-----------|
| `get` | `curl -sL` 获取完整页面信息 |
| `text` | `curl -sL | lynx -dump -stdin` 或 `html2text` |
| `title` | `curl -sL | grep -oP '<title>\K[^<]+'` |
| `links` | `curl -sL | grep -oP 'href="[^"]*"'` |
| `search` | `curl -sL | grep -c 'keyword'` 或 `grep -q` |

### 2.4 下载大小限制

- 响应体：`curl --max-filesize` 或 `curl | head -c 100M` 管道限制
- 配置项 `web.max_download_size`（默认 100MB）

## 阶段三：清理与收尾

### 3.1 移除 Python 依赖

从 `requirements.txt` 删除：

```diff
- requests
- beautifulsoup4
```

### 3.2 统一使用 SubprocessCommandExecutor

所有新工具统一通过 `SubprocessCommandExecutor` 执行命令，与 `CommandLineTool` 保持一致：
- 输出上限 1MB（超出自动终止进程并截断）
- 超时控制
- 中断取消（`cancel()` → `process.kill()`）

### 3.3 中断支持

- 利用已有的 `SubprocessCommandExecutor.cancel()` 机制
- 确保 Ctrl+C 可终止长时间运行的 curl 下载或文件操作

### 3.4 更新提示词

`~/.aibox/prompts/` 中相关文件更新：

| 文件 | 更新内容 |
|------|---------|
| `3_file_operations.md` | 文件操作描述改为命令行方式 |
| `4_web_access.md` | 网络访问描述改为 curl 方式 |

### 3.5 更新文档

- `SKILLS_FORMAT.md`：技能开发示例中文件/网络操作更新
- `README.md`：更新依赖说明和架构图

## 不做的事

- **不改变工具接口**：`read_file`、`write_file`、`list_files`、`web` 四个工具名称和参数签名保持不变
- **不改变 `CommandLineTool`（`run_command`）**：已有的命令执行工具不动
- **不改变 `SubprocessCommandExecutor`**：已有安全限制（1MB 输出）保持不变
- **不改变记忆/备忘录数据库**：`MemoryDatabase`、`MemoDatabase` 的 SQLite 操作保留

## 收益

- **减少依赖**：移除 `requests`、`beautifulsoup4` 两个 Python 库，降低供应链风险
- **统一执行模式**：所有外部操作（文件、网络、命令）全部走 `SubprocessCommandExecutor`，统一超时控制、输出限制、中断取消
- **更强的可审计性**：AI 操作日志中能看到完整的 `curl` / `cat` / `ls` 命令，更透明
- **复用已有基础设施**：`CommandLineTool` 已有的安全防护直接复用

## 风险与注意事项

| 风险 | 缓解措施 |
|------|----------|
| `curl` 不存在 | 启动时检测 `curl` 可用性，缺失时尝试 `wget` 降级或报错 |
| 跨平台 `sed`/`grep` 差异 | macOS (BSD) 和 Linux (GNU) 行为差异；使用 POSIX 兼容写法，必要时运行时检测 |
| 网页解析精度下降 | `grep` 正则提取 HTML 不可靠；优先用 `lynx -dump` 或 `html2text` 替代 BeautifulSoup 解析 |
| 命令注入风险 | 所有用户输入参数必须经过 `shlex.quote()` 转义 |
| Proxy 配置 | `curl` 通过 `--socks5` 或 `ALL_PROXY` 环境变量传递，与现有 `set_proxy` 工具集成 |
| 性能影响 | 文件操作每步 fork 新进程，但 aibox 不是高吞吐服务，IO 瓶颈远大于进程开销 |