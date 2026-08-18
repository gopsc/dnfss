#!/usr/bin/env python3
"""
tools.basic_tools - 基础工具集（A 批抽离）

抽离自 aibox.py 的无外部子系统依赖工具类，仅依赖 common 与标准库。
包含：
  - FileToolBase           : 文件操作工具基类
  - ReadFileTool           : 读文件（返回命令行指引）
  - WriteFileTool          : 写文件（返回命令行指引）
  - ListFilesTool          : 列目录（返回命令行指引）
  - SimpleWebCrawlerTool   : 网页访问（返回 curl 指引）
  - GetCurrentTimeTool     : 获取当前时间
  - CalculatorTool         : 数学计算

依赖方向：common <- tools.basic_tools，不反向依赖 aibox 任何符号。
"""

import os
import queue
import shlex
from datetime import datetime
from typing import Dict, Optional, Tuple

from common import ConfigManager, constants
from .base import Tool


# ==================== 文件操作工具基类 ====================

class FileToolBase(Tool):
    """文件操作工具基类"""

    def __init__(self, logger=None):
        super().__init__(logger)

    def normalize_path(self, filepath: str) -> str:
        return os.path.abspath(os.path.expanduser(filepath))

    def check_path_safety(self, filepath: str, check_exists: bool = False) -> Tuple[bool, str, str]:
        """检查路径安全性"""
        try:
            abs_path = self.normalize_path(filepath)

            if check_exists and not os.path.exists(abs_path):
                return False, abs_path, f"❌ 文件不存在: {abs_path}"

            # 检查文件大小
            if os.path.exists(abs_path) and os.path.isfile(abs_path):
                file_size = os.path.getsize(abs_path)
                max_size = constants.MAX_FILE_SIZE
                if file_size > max_size:
                    return False, abs_path, f"❌ 文件太大 ({file_size/1024/1024:.1f}MB)"

            return True, abs_path, ""

        except Exception as e:
            return False, filepath, f"❌ 路径检查错误: {str(e)}"


# ==================== 文件操作工具（修改版，支持流式输出） ====================

class ReadFileTool(FileToolBase):
    """读取文件内容工具（支持流式输出）"""

    def get_name(self) -> str:
        return "read_file"

    def get_description(self) -> str:
        return "读取文件内容，支持指定起始行和结束行，或读取开头/结尾"

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "encoding": {"type": "string", "default": "utf-8"},
                "start_line": {"type": "integer", "default": 1},
                "end_line": {"type": "integer", "default": 0},
                "max_lines": {"type": "integer", "default": 100},
                "head": {"type": "boolean", "default": False},
                "tail": {"type": "boolean", "default": False}
            },
            "required": ["filepath"]
        }

    def execute(self, filepath: str, encoding: str = "utf-8",
                start_line: int = 1, end_line: int = 0,
                max_lines: int = 100, head: bool = False,
                tail: bool = False, **kwargs) -> str:
        if error := self.validate_params(["filepath"], filepath=filepath):
            return error

        is_safe, abs_path, error = self.check_path_safety(filepath, check_exists=True)
        if not is_safe:
            return error

        # 限制max_lines上限，防止请求超大范围
        max_lines = min(max_lines, 500)
        # L1（审计 2026-08-18）：start/end_line 为 LLM 可控参数，此前未强转
        # 直拼 sed 单引号模板——LLM 传含引号的字符串可闭合引号注入命令。
        # 强转 int（非法输入在此拦截）并夹取合理范围。
        try:
            start_line = int(start_line)
            end_line = int(end_line)
        except (TypeError, ValueError):
            return "❌ 参数错误: start_line/end_line 必须为整数"
        start_line = max(1, start_line)
        end_line = max(0, min(end_line, 10_000_000))
        quoted = shlex.quote(abs_path)

        # 构造等价的命令行读取命令
        if head:
            cmd = f"head -n {max_lines} {quoted}"
        elif tail:
            cmd = f"tail -n {max_lines} {quoted}"
        elif end_line > 0:
            cmd = f"sed -n '{start_line},{end_line}p' {quoted}"
        elif max_lines > 0:
            end = start_line + max_lines - 1
            cmd = f"sed -n '{start_line},{end}p' {quoted}"
        else:
            cmd = f"sed -n '{start_line},$p' {quoted}"

        # 返回 markdown 提示词，指导智能体用命令行工具读取文件
        prompt = f"""请使用 `run_command` 工具执行以下命令来读取文件内容，不要直接返回文件内容：

```bash
{cmd}
```

- 若需统计总行数：`wc -l {quoted}`
- 若需查看文件编码：`file -b --mime-encoding {quoted}`
- 若单行超长，可在命令后追加 `| cut -c 1-10000` 截断到 10000 字符
- 文件大小上限 {constants.MAX_FILE_SIZE} 字节，单次最多读取 500 行"""
        return prompt

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行文件读取"""
        filepath = kwargs.get("filepath")

        # 发送开始执行的消息
        output_queue.put(("line", f"\n📄 读取文件: {filepath}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 执行操作
        result = self.execute(**kwargs)

        # 发送结果
        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class WriteFileTool(FileToolBase):
    """写入文件工具（支持流式输出）"""

    def get_name(self) -> str:
        return "write_file"

    def get_description(self) -> str:
        return "文件写入工具 - 支持覆盖、追加"

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["w", "a"], "default": "w"},
                "encoding": {"type": "string", "default": "utf-8"}
            },
            "required": ["filepath", "content"]
        }

    def execute(self, filepath: str = None, content: str = None, mode: str = "w",
                encoding: str = "utf-8", **kwargs) -> str:
        if error := self.validate_params(["filepath", "content"], filepath=filepath, content=content):
            return error

        is_safe, abs_path, error = self.check_path_safety(filepath)
        if not is_safe:
            return error

        quoted = shlex.quote(abs_path)
        directory = os.path.dirname(abs_path)
        dir_cmd = f"mkdir -p {shlex.quote(directory)}" if directory else ""

        # 内容写入使用 heredoc，避免引号/特殊字符转义问题
        # M1 修复（审计 2026-08-17）：定界符唯一化——若 content 中含独立行
        # 与定界符相同（如 "AIBOX_EOF"），heredoc 会提前终止，其后内容被
        # 当作 shell 命令执行（注入）。冲突时递增后缀直到唯一。
        delimiter = "AIBOX_EOF"
        content_lines = {line.strip() for line in content.split("\n")}
        while delimiter in content_lines:
            delimiter += "_X"
        redirect = ">" if mode == "w" else ">>"
        write_cmd = f"cat {redirect} {quoted} <<'{delimiter}'\n{content}\n{delimiter}"
        if dir_cmd:
            write_cmd = f"{dir_cmd} && {write_cmd}"

        # 返回 markdown 提示词，指导智能体用命令行工具写入文件
        prompt = f"""请使用 `run_command` 工具执行以下命令来写入文件，不要在此工具中直接写入：

```bash
{write_cmd}
```

- 如遇 heredoc 内容含特殊字符导致解析失败，可用 `printf '%s' '内容' > {quoted}` 覆盖写入
- 若需检查文件大小：`wc -c {quoted}`
- 文件大小上限 {constants.MAX_FILE_SIZE} 字节"""
        return prompt

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行文件写入"""
        filepath = kwargs.get("filepath")

        # 发送开始执行的消息
        output_queue.put(("line", f"\n📝 写入文件: {filepath}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 执行操作
        result = self.execute(**kwargs)

        # 发送结果
        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class ListFilesTool(FileToolBase):
    """列出目录内容工具（支持流式输出）"""

    def get_name(self) -> str:
        return "list_files"

    def get_description(self) -> str:
        return "列出指定目录下的文件和子目录"

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "default": "."},
                "show_hidden": {"type": "boolean", "default": False}
            }
        }

    def execute(self, directory: str = ".", show_hidden: bool = False, **kwargs) -> str:
        is_safe, abs_path, error = self.check_path_safety(directory)
        if not is_safe:
            return error

        if not os.path.exists(abs_path):
            return f"❌ 目录不存在: {abs_path}"

        if not os.path.isdir(abs_path):
            return f"❌ 路径不是目录: {abs_path}"

        quoted = shlex.quote(abs_path)
        flag = "-la" if show_hidden else "-l"

        # 返回 markdown 提示词，指导智能体用命令行工具列目录
        prompt = f"""请使用 `run_command` 工具执行以下命令来列出目录内容，不要直接在此工具中列出：

```bash
ls {flag} {quoted}
```

- 若需区分目录/文件并显示大小：`ls -lhF {quoted}`
- 若需统计目录总大小：`du -sh {quoted}`
- 若需递归列出：`find {quoted} -maxdepth 2`
- 若需了解文件类型：`file {quoted}/*`"""
        return prompt

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行列出目录"""
        directory = kwargs.get("directory", ".")

        # 发送开始执行的消息
        output_queue.put(("line", f"\n📁 列出目录: {directory}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 执行操作
        result = self.execute(**kwargs)

        # 发送结果
        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


# ==================== 网页访问工具 ====================

class SimpleWebCrawlerTool(Tool):
    """简化版网页爬虫工具（返回 markdown 提示词，指导改用 curl 命令行）"""

    def __init__(self, logger=None):
        super().__init__(logger)

    def get_name(self) -> str:
        return "web"

    def get_description(self) -> str:
        return """简单易用的网页访问工具 - 一句话搞定！
支持：获取网页、提取内容、搜索关键词、下载文件"""

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "text", "title", "links", "search"]
                },
                "url": {"type": "string"},
                "keyword": {"type": "string"}
            },
            "required": ["action", "url"]
        }

    def _proxy_curl_args(self) -> str:
        """根据当前代理配置构造 curl --socks5 参数"""
        proxies = ConfigManager().get_proxy()
        if not proxies:
            return ""
        proxy_url = proxies.get("https") or proxies.get("http") or ""
        host_port = proxy_url.split("://", 1)[-1]
        if "@" in host_port:
            host_port = host_port.split("@", 1)[-1]
        return f"--socks5 {shlex.quote(host_port)}"

    def execute(self, action: str, url: str, **kwargs) -> str:
        if action not in ("get", "text", "title", "links", "search"):
            return f"❌ 未知操作: {action}"

        # L10（审计 2026-08-18）：url 为 LLM 可控输入（可被网页内容诱导），
        # 此前不限 scheme——file:// 可经生成的 curl 指引读取本地文件（SSRF）。
        # 白名单 http/https 后其余协议直接拒绝。
        if not url or not str(url).strip().lower().startswith(("http://", "https://")):
            return "❌ 仅支持 http/https 协议地址（已拦截 file:// 等非网络协议）"

        quoted_url = shlex.quote(url)
        timeout = constants.WEB_TIMEOUT
        user_agent = shlex.quote(constants.WEB_USER_AGENT)
        proxy = self._proxy_curl_args()
        base = f"curl -sL --max-time {timeout} -A {user_agent} {proxy}".strip()

        if action == "get":
            cmd = f"{base} -w '\\nHTTP_CODE:%{{http_code}}' {quoted_url}"
            extra = "如需查看响应头：`curl -sIL ...`；如需保存到文件：`curl -sL ... -o 文件名`"
        elif action == "text":
            cmd = f"{base} {quoted_url} | sed -r 's/<[^>]+>//g'"
            extra = "若系统装有 lynx，可用 `lynx -dump -stdin` 提取纯文本更准确"
        elif action == "title":
            cmd = f"{base} {quoted_url} | grep -oP '<title>\\K[^<]+'"
            extra = "无输出则表示未找到标题"
        elif action == "links":
            cmd = f"{base} {quoted_url} | grep -oP 'href=\"\\K[^\"]+'"
            extra = "输出所有链接 URL，可配合 `head` 限制数量"
        else:  # search
            keyword = kwargs.get("keyword")
            if not keyword:
                return "❌ 需要提供 keyword 参数"
            cmd = f"{base} {quoted_url} | grep -c {shlex.quote(keyword)}"
            extra = f"返回匹配行数，大于 0 即表示找到关键词 '{keyword}'"

        # 返回 markdown 提示词，指导智能体用 curl 完成网页访问
        prompt = f"""请使用 `run_command` 工具执行以下命令来完成网页操作，不要直接返回网页内容：

```bash
{cmd}
```

{extra}
- 下载大小限制 {constants.WEB_MAX_DOWNLOAD_SIZE} 字节，可在命令后追加 `| head -c {constants.WEB_MAX_DOWNLOAD_SIZE}` 限制
- 当前已按配置自动附带代理：{proxy or '无（如需代理请先调用 set_proxy 工具）'}"""
        return prompt

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行网页操作"""
        action = kwargs.get("action")
        url = kwargs.get("url")

        # 发送开始执行的消息
        output_queue.put(("line", f"\n🌐 执行网页操作: {action} - {url}\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        # 执行操作
        result = self.execute(**kwargs)

        # 发送结果
        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


# ==================== 系统工具（修改版，支持流式输出） ====================

class GetCurrentTimeTool(Tool):
    """获取当前时间工具（支持流式输出）"""

    def get_name(self) -> str:
        return "get_current_time"

    def get_description(self) -> str:
        return "获取当前日期和时间"

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["full", "date", "time"], "default": "full"}
            }
        }

    def execute(self, format: str = "full", **kwargs) -> str:
        now = datetime.now()

        if format == "date":
            result = now.strftime("%Y-%m-%d")
        elif format == "time":
            result = now.strftime("%H:%M:%S")
        else:
            result = now.strftime(constants.DATETIME_FORMAT)

        return self.format_result(True, "当前时间", {"时间": result})

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行获取时间"""
        output_queue.put(("line", "\n🕐 获取当前时间\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))


class CalculatorTool(Tool):
    """计算器工具（支持流式输出）"""

    def get_name(self) -> str:
        return "calculator"

    def get_description(self) -> str:
        return "执行数学计算"

    def get_parameters(self) -> Dict:
        return {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
                "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]}
            },
            "required": ["a", "b", "operation"]
        }

    def execute(self, a: float, b: float, operation: str, **kwargs) -> str:
        operations = {
            "add": ("+", a + b),
            "subtract": ("-", a - b),
            "multiply": ("*", a * b),
            "divide": ("/", a / b if b != 0 else None)
        }

        if operation not in operations:
            return f"❌ 未知运算: {operation}"

        op_symbol, result = operations[operation]

        if result is None:
            return "❌ 错误：除数不能为0"

        data = {
            "表达式": f"{a} {op_symbol} {b}",
            "结果": result
        }

        return self.format_result(True, "计算成功", data)

    def execute_streaming(self, output_queue: queue.Queue, **kwargs):
        """流式执行计算"""
        output_queue.put(("line", "\n🧮 执行计算\n"))
        output_queue.put(("line", "-" * 50 + "\n"))

        result = self.execute(**kwargs)

        output_queue.put(("line", result + "\n"))
        output_queue.put(("complete", result))
