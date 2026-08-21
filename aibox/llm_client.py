# -*- coding: utf-8 -*-
"""
大模型客户端抽象层

为 aibox 提供统一的 LLM 调用接口，将「调用哪个厂商 / 哪个模型」与业务逻辑解耦。
业务层只依赖 `LLMClient` 抽象方法与统一的流式事件格式，切换厂商只需：
  1. 新增一个 `LLMClient` 子类实现；
  2. 在 `create_llm_client` 工厂里按 `api.provider` 进行路由。

当前内置实现：
  - `DeepSeekClient`：兼容 OpenAI Chat Completions 协议（DeepSeek / Kimi / Qwen / GLM / Doubao 等绝大多数厂商均可复用）。

统一流式事件格式（`stream_chat` 逐条 yield 的 dict）：
  {"type": "content", "text": str}      # 增量正文
  {"type": "reasoning", "text": str}    # 增量思考内容（可选，厂商支持时）
  {"type": "tool_call", "index": int, "id": str, "name": str, "arguments": str}  # 工具调用增量
  {"type": "done"}                      # 流结束
"""

from abc import ABC, abstractmethod
from typing import Dict, Iterator, List, Optional

import json
import os
import re
import time
import requests

# L12（审计 2026-08-18）：瞬时错误体可能回显网关/WAF 错误页中的
# Authorization 头，抹掉 Bearer 令牌后再进日志，防密钥落盘
_AUTH_HEADER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")


def _sanitize_log_msg(msg: str) -> str:
    """日志脱敏：替换错误消息中的 Bearer 令牌。"""
    if not msg:
        return msg
    return _AUTH_HEADER_RE.sub("Bearer ***", msg)


class LLMClient(ABC):
    """大模型客户端抽象基类。子类需实现流式/非流式两种调用，并归一化为统一事件格式。"""

    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: int, config=None, logger=None,
                 reasoning_enabled: bool = None,
                 display_name: str = None, default_temperature: float = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.config = config          # 可选，用于读取代理等动态配置
        self.logger = logger          # 可选，用于日志输出
        # M3：档案级思考模式开关为构造契约；None = 未配置（消费方回退全局 api.reasoning_enabled）
        self.reasoning_enabled = reasoning_enabled
        # L1：展示名（默认取档案名），业务层上下文（如 history 的 assistant 名）不再误标 "DeepSeek"
        self._display_name = display_name
        # L3：档案级默认采样温度；None = 未配置（方法参数未显式传入时回退硬编码默认值）
        self.default_temperature = default_temperature
        # 当前活动的 HTTP 响应引用，供 cancel() 强制关闭底层连接以中断阻塞读取
        self._current_response = None

    @property
    def name(self) -> str:
        """厂商/模型展示名，业务层可用于上下文（如 history 里的 assistant 名）。"""
        return self._display_name or "LLM"

    # ---------- 工具方法 ----------

    def _resolve_proxies(self) -> Optional[Dict[str, str]]:
        """从 config 解析 requests 可用的 proxies 字典（动态代理）。"""
        if self.config is not None and hasattr(self.config, "get_proxy"):
            return self.config.get_proxy()
        return None

    def _log(self, level: str, msg: str):
        if self.logger is not None and hasattr(self.logger, level):
            try:
                getattr(self.logger, level)(_sanitize_log_msg(msg))
            except Exception:
                pass

    def cancel(self):
        """中断当前流式请求。线程安全，可从任意线程调用。

        注：用户中断标志由 FailoverClient 维护（其 cancel() 广播整条链并置
        自身 _cancelled，据此跳过转移）——底层 client 只负责强关连接。
        """
        resp = self._current_response
        self._current_response = None
        if resp is not None:
            try:
                # 先关闭底层 raw socket，强制阻塞的 recv() 立即抛出异常
                if getattr(resp, "raw", None) is not None:
                    resp.raw.close()
            except Exception:
                pass
            try:
                resp.close()
            except Exception:
                pass

    # ---------- 抽象接口 ----------

    @abstractmethod
    def chat_completion(self, messages: List[Dict], temperature: float = None,
                        max_tokens: int = 1024, **kwargs) -> str:
        """非流式对话，返回完整文本。用于对话压缩等离线摘要场景。
        temperature=None 时按 default_temperature（档案级）→ 0.3 回退。"""

    @abstractmethod
    def stream_chat(self, messages: List[Dict], temperature: float = None,
                    max_tokens: int = 8192, tools: Optional[List[Dict]] = None,
                    tool_choice: Optional[str] = None,
                    reasoning_enabled: bool = False, **extra) -> Iterator[Dict]:
        """流式对话，逐条 yield 统一事件格式的 dict。业务层据此驱动输出/工具调用。
        temperature=None 时按 default_temperature（档案级）→ 0.7 回退。"""


class DeepSeekClient(LLMClient):
    """DeepSeek 客户端（OpenAI Chat Completions 协议兼容）。

    思考模式通过 `thinking: {"type": "enabled"}` 参数控制，思考内容字段为
    `delta.reasoning_content`。二者均为 DeepSeek 特色，其他 OpenAI 兼容厂商
    若字段不同，可在子类中覆写 `_parse_delta` 归一化。
    """

    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: int = 30, config=None, logger=None,
                 reasoning_enabled: bool = None,
                 display_name: str = None, default_temperature: float = None):
        super().__init__(api_key, model, base_url, timeout, config, logger,
                         reasoning_enabled=reasoning_enabled,
                         display_name=display_name,
                         default_temperature=default_temperature)

    @property
    def name(self) -> str:
        # L1：档案命中时展示档案名（或档案 display_name），旧扁平节保持 "DeepSeek"
        return self._display_name or "DeepSeek"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat_completion(self, messages: List[Dict], temperature: float = None,
                        max_tokens: int = 1024, **kwargs) -> str:
        # L3：None → 档案级 default_temperature → 硬编码默认（与旧版行为一致）
        if temperature is None:
            temperature = self.default_temperature if self.default_temperature is not None else 0.3
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        payload.update(kwargs)
        try:
            response = requests.post(
                self.base_url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                proxies=self._resolve_proxies(),
            )
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            if response.status_code == 429 or response.status_code >= 500:
                # L4a：瞬时故障显式抛出——FailoverClient 据此转移下一档案；
                # 无包装器时由调用方 catch-all 接住，下游仍走失败分支（语义等价）
                raise TransientLLMError(
                    f"LLM 请求瞬时失败: HTTP {response.status_code}: {response.text[:200]}",
                    retry_after=_retry_after_of(response))
            self._log("error", f"LLM 压缩请求失败: HTTP {response.status_code}")
            return None
        except TransientLLMError:
            raise  # L4a：瞬时故障上抛供 FailoverClient 转移，不吞
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            raise  # L4a：连接类瞬时故障同样上抛（原被吞成 None，无法转移）
        except Exception as e:
            self._log("error", f"LLM 压缩请求异常: {e}")
            return None

    def stream_chat(self, messages: List[Dict], temperature: float = None,
                    max_tokens: int = 8192, tools: Optional[List[Dict]] = None,
                    tool_choice: Optional[str] = None,
                    reasoning_enabled: bool = False, **extra) -> Iterator[Dict]:
        # L3：None → 档案级 default_temperature → 硬编码默认（与旧版行为一致）
        if temperature is None:
            temperature = self.default_temperature if self.default_temperature is not None else 0.7
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        # 思考模式
        if reasoning_enabled:
            payload["thinking"] = {"type": "enabled"}
        # 工具调用
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        payload.update(extra)

        proxies = self._resolve_proxies()
        response = requests.post(
            self.base_url,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
            stream=True,
            proxies=proxies,
        )
        self._current_response = response

        if response.status_code != 200:
            if response.status_code == 429 or response.status_code >= 500:
                # L4b：瞬时故障显式分类（此时尚未产出任何事件，可安全转移重放）
                raise TransientLLMError(
                    f"LLM 流式请求瞬时失败: HTTP {response.status_code}: {response.text[:200]}",
                    retry_after=_retry_after_of(response))
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:200]}")

        try:
            for line in response.iter_lines():
                if not line:
                    continue
                line_text = line.decode("utf-8", errors="replace")  # L3：非 UTF-8 行不中断流
                if not line_text.startswith("data: "):
                    continue
                data = line_text[6:]
                if data == "[DONE]":
                    yield {"type": "done"}
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as e:
                    self._log("error", f"JSON 解析错误: {e}, 数据: {data}")
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                for event in self._parse_delta(delta, reasoning_enabled):
                    yield event
        finally:
            self._current_response = None

    # L5：思考内容的常见厂商字段别名（首个命中的非空字段生效）
    REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking", "thinking_content")

    def _parse_delta(self, delta: Dict, reasoning_enabled: bool) -> Iterator[Dict]:
        """将单条 delta 归一化为统一事件。子类覆写以适配不同厂商字段。"""
        # 思考内容：DeepSeek reasoning_content / OpenRouter reasoning /
        # 部分 GLM thinking(_content) —— 此前仅识别 DeepSeek 字段，其他厂商思考流静默丢失
        if reasoning_enabled:
            for field in self.REASONING_FIELDS:
                value = delta.get(field)
                if value:
                    yield {"type": "reasoning", "text": value}
                    break
        # 正文
        if delta.get("content"):
            yield {"type": "content", "text": delta["content"]}
        # 工具调用（增量）
        for tc in delta.get("tool_calls", []):
            index = tc.get("index", 0)
            function = tc.get("function", {})
            yield {
                "type": "tool_call",
                "index": index,
                "id": tc.get("id", ""),
                "name": function.get("name", ""),
                "arguments": function.get("arguments", ""),
            }


class TransientLLMError(RuntimeError):
    """瞬时 LLM 故障（HTTP 429/5xx）——FailoverClient 据此转移下一档案（L4a）。

    L4c：retry_after 携带服务端 Retry-After 响应头（数字秒）建议冷却时长，
    FailoverClient 以其替代默认冷却期（429 限流时尊重服务端节奏）。
    """

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after_of(response) -> Optional[float]:
    """从 Retry-After 响应头解析冷却秒数（仅支持数字秒格式，其余忽略返回 None）。

    P2：上限 cap 3600s——异常/恶意服务端返回超大值（如 999999）时，
    避免档案被单条响应头冷却数天。
    """
    try:
        v = response.headers.get("Retry-After")
        if v is None:
            return None
        return min(max(1.0, float(v)), 3600.0)
    except (TypeError, ValueError, AttributeError):
        return None


class FailoverClient:
    """跨档案失败转移包装器（L4a）。

    持有序 client 链（主档案 + fallback 档案），对外实现与 LLMClient 相同的
    消费面（未显式定义的属性经 __getattr__ 委托主 client）。

    L4a 范围——chat_completion（非流式幂等）按链转移：
      - 转移：HTTP 429/5xx（TransientLLMError）、连接失败/超时（requests 连接类异常）
      - 不转移：认证/参数类 4xx（底层返回 None，直接透传——配置问题应响亮暴露）
    失败档案进入冷却期（cooldown_seconds，默认 60s），期间直接跳过，
    避免每次请求都撞刚失败的端点。链耗尽返回 None（与 chat_completion 失败契约一致）。

    L4b 范围——stream_chat 仅**首事件前**故障转移（连接失败/HTTP 429/5xx，
    尚未产出任何 reasoning/content/tool_call 事件，重放对用户不可见）；
    已产出事件后失败直接上抛（重放会导致前端重复输出/工具增量错乱）；
    用户中断（cancel() 置位）引发的连接异常绝不转移。

    L4c 范围——可观测性与预算控制：
      - get_stats() 暴露 requests/failovers/cooldown_skips/exhausted 聚合计数
      - on_failover(from_name, to_name, reason) 回调（to_name=None 表示链耗尽），
        供管理面/前端通知接入，回调异常不影响主流程
      - timeout_budget 限制整条链尝试的总秒数（缺省不限），耗尽即停止尝试剩余档案，
        防止长链故障时超时叠加（3 档案最坏 3×timeout）
      - 429 响应带 Retry-After（数字秒）时按其延长该档案冷却期

    并发契约（P4）：实例非线程安全（_cancelled/_call_started/_stats 无锁）。
    当前调用面满足"单线程串行调用 + cancel() 可从其他线程"的模式（compressor
    在 _msg_lock 内同步调用、子代理各持独立实例）；如需多线程共享调用，
    需由调用方加锁或为实例补锁。
    """

    def __init__(self, clients: List, logger=None, cooldown_seconds: float = 60.0,
                 timeout_budget: Optional[float] = None, on_failover=None):
        self._clients = list(clients)
        self.logger = logger
        self.cooldown_seconds = cooldown_seconds
        self._fail_at = {}  # client.name → (失败时刻, 冷却秒数)
        self._cancelled = False
        self._stats = {"requests": 0, "failovers": 0, "cooldown_skips": 0, "exhausted": 0}
        if timeout_budget is not None and timeout_budget <= 0:
            self._log("warning", f"timeout_budget 非法（{timeout_budget} <= 0），按未配置处理")
            timeout_budget = None
        self.timeout_budget = timeout_budget
        self.on_failover = on_failover
        self._call_started = None

    def __getattr__(self, item):
        # 未显式定义的属性/方法委托主 client（name/model/api_key/cancel 等）；
        # 下划线属性直接报错，防 __init__ 前访问 self._clients 的无限递归
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._clients[0], item)

    def _log(self, level: str, msg: str):
        if self.logger is not None and hasattr(self.logger, level):
            try:
                getattr(self.logger, level)(_sanitize_log_msg(msg))
            except Exception:
                pass

    def _cooling(self, client) -> bool:
        entry = self._fail_at.get(client.name)
        if entry is None:
            return False
        t, cd = entry
        return (time.monotonic() - t) < cd

    def _mark_fail(self, client, cooldown: Optional[float] = None):
        # L4c：429 Retry-After（经 TransientLLMError.retry_after）覆盖默认冷却时长
        self._fail_at[client.name] = (time.monotonic(),
                                      cooldown if cooldown and cooldown > 0
                                      else self.cooldown_seconds)

    def _budget_exhausted(self) -> bool:
        if self.timeout_budget is None or self._call_started is None:
            return False
        return (time.monotonic() - self._call_started) >= self.timeout_budget

    def get_stats(self) -> Dict:
        """L4c：转移统计快照（requests/failovers/cooldown_skips/exhausted）。"""
        return dict(self._stats)

    def _notify_failover(self, from_name: str, to_name, reason: str):
        self._stats["failovers"] += 1
        try:
            if self.on_failover is not None:
                self.on_failover(from_name, to_name, _sanitize_log_msg(reason))
        except Exception as e:
            self._log("warning", f"on_failover 回调异常（忽略）: {e}")

    def cancel(self):
        """L4b：中断链上全部 client（任一可能正在流式中）并置自身中断标志。

        显式定义以覆盖 __getattr__ 委托——包装器必须同时通知整条链。
        """
        self._cancelled = True
        for client in self._clients:
            try:
                client.cancel()
            except Exception:
                pass

    def chat_completion(self, messages: List[Dict], temperature: float = None,
                        max_tokens: int = 1024, **kwargs):
        self._cancelled = False  # 新一轮请求，清除上一轮中断标志
        self._call_started = time.monotonic()
        self._stats["requests"] += 1
        last_exc = None
        tried = 0
        for idx, client in enumerate(self._clients):
            if self._cooling(client):
                self._stats["cooldown_skips"] += 1
                self._log("info", f"模型档案 {client.name} 处于失败冷却期，跳过")
                continue
            if self._budget_exhausted():
                self._log("warning",
                          f"失败转移时间预算（{self.timeout_budget}s）已耗尽，停止尝试剩余档案")
                break
            tried += 1
            try:
                result = client.chat_completion(messages, temperature=temperature,
                                                max_tokens=max_tokens, **kwargs)
                if result is not None:
                    return result
                # None：认证/参数类 4xx 或响应异常——不可转移，直接透传
                return None
            except (TransientLLMError, requests.exceptions.RequestException) as e:
                if self._cancelled:
                    return None  # 用户中断引发的异常：不转移
                last_exc = e
                self._mark_fail(client, cooldown=getattr(e, "retry_after", None))
                to_name = (self._clients[idx + 1].name
                           if idx + 1 < len(self._clients) else None)
                self._notify_failover(client.name, to_name, str(e))
                self._log("warning", f"模型档案 {client.name} 瞬时故障（{e}），转移下一档案")
        self._stats["exhausted"] += 1  # 末尾统一计数（链耗尽/预算耗尽/全冷却）
        self._log("error", f"全部 {len(self._clients)} 个模型档案均不可用（尝试 {tried} 个）：{last_exc}")
        return None

    def stream_chat(self, messages: List[Dict], temperature: float = None,
                    max_tokens: int = 8192, tools: Optional[List[Dict]] = None,
                    tool_choice: Optional[str] = None,
                    reasoning_enabled: bool = False, **extra) -> Iterator[Dict]:
        """L4b：流式转移——仅首事件前故障可安全重放（对用户不可见）。"""
        self._cancelled = False  # 新一轮请求，清除上一轮中断标志
        self._call_started = time.monotonic()
        self._stats["requests"] += 1
        last_exc = None
        for idx, client in enumerate(self._clients):
            if self._cooling(client):
                self._stats["cooldown_skips"] += 1
                self._log("info", f"模型档案 {client.name} 处于失败冷却期，跳过")
                continue
            if self._budget_exhausted():
                self._log("warning",
                          f"失败转移时间预算（{self.timeout_budget}s）已耗尽，停止尝试剩余档案")
                break
            produced_any = False
            try:
                for event in client.stream_chat(
                        messages, temperature=temperature, max_tokens=max_tokens,
                        tools=tools, tool_choice=tool_choice,
                        reasoning_enabled=reasoning_enabled, **extra):
                    produced_any = True
                    yield event
                return  # 自然结束（含 done 事件）视为成功
            except (TransientLLMError, requests.exceptions.RequestException) as e:
                if self._cancelled:
                    raise  # 用户中断（cancel 强关连接）：绝不转移
                if produced_any:
                    raise  # 已向消费方产出事件，重放会重复输出/工具增量错乱
                last_exc = e
                self._mark_fail(client, cooldown=getattr(e, "retry_after", None))
                to_name = (self._clients[idx + 1].name
                           if idx + 1 < len(self._clients) else None)
                self._notify_failover(client.name, to_name, str(e))
                self._log("warning",
                          f"模型档案 {client.name} 流式瞬时故障（首事件前，{e}），转移下一档案")
        self._stats["exhausted"] += 1
        if last_exc is not None:
            raise last_exc  # 链耗尽：抛最后异常，由既有错误处理（回滚/报错）接住
        raise RuntimeError(
            f"全部 {len(self._clients)} 个模型档案不可用（冷却期或转移时间预算耗尽）")


def resolve_api_key(api_key: Optional[str], config=None,
                    env_name: Optional[str] = None,
                    allow_missing: bool = False,
                    allow_key_file: bool = True) -> str:
    """解析 API Key：优先显式传入，其次环境变量，最后 ~/.cert/deepseek.key。

    env_name: 档案级环境变量名（来自 models.<name>.api_key_env）；
              None 时回退 config 的 api.api_key_env。
    allow_missing: 本地免密端点（models.<name>.api_key_env 为空串）时，
              允许无 Key，返回空串（Bearer 空头，Ollama/vLLM 忽略）。
    allow_key_file: 是否允许回退读取 ~/.cert/deepseek.key。该文件是 DeepSeek
              专属凭据，模型档案端点（其他厂商）必须传 False——环境变量取不到
              时直接报错，绝不把 A 厂商 Key 发给 B 厂商（跨厂商凭据泄漏）。
    """
    if api_key:
        return api_key
    if env_name is None:
        if config is not None and hasattr(config, "get"):
            env_name = config.get("api.api_key_env")
        env_name = env_name or "DEEPSEEK_API_KEY"
    if not env_name:  # 档案显式置空 → 免密端点
        if allow_missing:
            return ""
        raise RuntimeError("模型档案 api_key_env 配置为空且未启用免密")
    env_key = os.environ.get(env_name)
    if env_key:
        return env_key
    if allow_missing:
        return ""
    if not allow_key_file:
        raise RuntimeError(
            f"模型档案端点缺少 API Key：请设置环境变量 {env_name}，"
            f"或将该档案的 api_key_env 配置为空串以启用本地免密端点")
    try:
        # dict.get 的 default 参数会无条件先求值，直接写 os.environ["USERPROFILE"]
        # 在无该变量的 Linux 上必抛 KeyError，导致 key 文件回退路径永远不可达
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
        key_path = os.path.join(home, ".cert", "deepseek.key")
        with open(key_path, "r") as f:
            return f.read().strip()
    except Exception:
        raise RuntimeError(
            f"请设置环境变量 {env_name} 或在配置文件中设置 API Key")


def _validate_profile(name: str, profile) -> None:
    """H2：档案类型与 api_url 校验（在读取档案字段之前调用）。"""
    if not isinstance(profile, dict):
        raise ValueError(
            f"模型档案 '{name}' 配置无效：应为对象（字典），"
            f"实际为 {type(profile).__name__}，请检查 config.json 的 models.{name}")
    if not str(profile.get("api_url") or "").strip():
        raise ValueError(
            f"模型档案 '{name}' 缺少 api_url 字段，"
            f"请在 config.json 的 models.{name} 中补齐（OpenAI Chat Completions 端点 URL）")
    fb = profile.get("fallback")
    if fb is not None and (not isinstance(fb, list) or not all(isinstance(x, str) for x in fb)):
        raise ValueError(
            f"模型档案 '{name}' 的 fallback 字段无效：应为档案名字符串列表"
            f"（如 [\"kimi\", \"local\"]），实际为 {type(fb).__name__}，"
            f"请检查 config.json 的 models.{name}")


def _require_model(name: str, effective_model) -> None:
    """H2：有效模型名校验——缺 model 会把档案名当模型名发给 API（或 "model": null → 400）。"""
    if not str(effective_model or "").strip():
        raise ValueError(
            f"模型档案 '{name}' 缺少 model 字段（且未传入裸模型名），"
            f"请在 config.json 的 models.{name} 中补齐，或调用时用 --model 指定模型名")


def _resolve_model_profile(config, model: Optional[str]):
    """解析模型档案。返回 (profile_dict, effective_model_name, profile_name)。

    优先级：model 命中 models.<name> 档案 > api.default_model 档案 > None（回退旧 api 节）。
    model 未命中档案时视为裸模型名（同端点换模型，用默认档案的 url/key，模型名用裸名）。

    L2 前缀逃逸（档案名与裸模型名歧义时使用）：
      - "model:NAME" 强制按裸模型名解析（档案恰好叫 NAME 时仍走裸名语义）
      - "profile:NAME" 强制按档案名解析（不存在则报错，不再静默落回裸名语义）
    """
    if config is None or not hasattr(config, "get"):
        return None, model, None
    models = config.get("models") or {}
    if not isinstance(models, dict):
        return None, model, None

    force_bare = False
    if isinstance(model, str) and ":" in model:
        prefix, _, rest = model.partition(":")
        if prefix == "model":
            if not rest.strip():
                raise ValueError("model: 前缀后为空，请提供裸模型名，如 model:deepseek-chat")
            model, force_bare = rest, True
        elif prefix == "profile":
            if not rest.strip():
                raise ValueError("profile: 前缀后为空，请提供档案名，如 profile:kimi")
            prof = models.get(rest)
            if prof is None:
                raise ValueError(
                    f"模型档案 '{rest}' 不存在，可用档案：{', '.join(models) or '（无）'}")
            _validate_profile(rest, prof)
            effective = prof.get("model")
            _require_model(rest, effective)
            return prof, effective, rest

    explicit = models.get(model) if (model and not force_bare) else None
    default_name = config.get("api.default_model")
    default_profile = models.get(default_name) if default_name else None

    if explicit is not None:
        # model 是档案名 → 完整采用该档案（url/key/模型名）；
        # 档案缺 model 属配置错误，直接报错（不再把档案名当模型名发出）
        _validate_profile(model, explicit)
        effective = explicit.get("model")
        _require_model(model, effective)
        return explicit, effective, model
    if default_profile is not None:
        # model 是裸名或未指定 → 默认档案的 url/key，模型名被裸名覆盖
        _validate_profile(default_name, default_profile)
        effective = model or default_profile.get("model")
        _require_model(default_name, effective)
        return default_profile, effective, default_name
    return None, model, None


def _construct_profile_client(config, profile: Dict, profile_name: str,
                              effective_model: str, logger=None,
                              api_key: Optional[str] = None) -> LLMClient:
    """L4a：按档案构造单个 DeepSeekClient（主路径与 fallback 链复用，不处理 fallback 字段）。"""
    env_name = profile.get("api_key_env")
    allow_missing = (env_name == "")  # 档案显式空串 → 本地免密端点
    resolved_key = resolve_api_key(api_key, config, env_name=env_name,
                                   allow_missing=allow_missing, allow_key_file=False)
    return DeepSeekClient(
        api_key=resolved_key,
        model=effective_model,
        base_url=profile.get("api_url"),
        timeout=profile.get("timeout") or config.get("api.timeout", 30),
        config=config,
        logger=logger,
        reasoning_enabled=profile.get("reasoning_enabled",
                                      config.get("api.reasoning_enabled", False)),
        display_name=profile.get("display_name") or profile_name,
        default_temperature=profile.get("temperature"),
    )


def create_llm_client(config, api_key: Optional[str] = None,
                      logger=None, model: Optional[str] = None) -> LLMClient:
    """根据配置创建对应厂商的 LLM 客户端。

    模型选择优先级：
      1. model 命中 models 节档案名 → 采用该档案（api_url/model/api_key_env/timeout）
      2. api.default_model 指向的档案 → model 为裸模型名时覆盖档案内模型名
      3. 均未配置 → 回退旧 api 扁平节（deepseek_api_url/deepseek_model）
    L2 前缀逃逸："model:NAME" 强制裸名 / "profile:NAME" 强制档案（--model 同样支持）。

    provider: deepseek / openai_compatible / openai 均映射到 DeepSeekClient
    （OpenAI Chat Completions 协议兼容实现，厂商差异由子类覆写 _parse_delta）。

    Args:
        config: 配置对象（需提供 get 方法）。
        api_key: 显式传入的 API Key；为 None 时按 resolve_api_key 解析。
        logger: 可选日志对象。
        model: 档案名或裸模型名（支持 model:/profile: 前缀逃逸）。
    """
    profile, model, profile_name = _resolve_model_profile(config, model)

    if profile is None:
        # 旧 api 扁平节（唯一允许回退 ~/.cert/deepseek.key 的路径，H1）
        provider = (config.get("api.provider", "deepseek") or "deepseek").lower()
        base_url = config.get("api.deepseek_api_url")
        model = model or config.get("api.deepseek_model", "deepseek-v4-flash")
        timeout = config.get("api.timeout", 30)
        resolved_key = resolve_api_key(api_key, config, env_name=None,
                                       allow_missing=False, allow_key_file=True)
    else:
        provider = "openai_compatible"
        primary = _construct_profile_client(config, profile, profile_name,
                                            model, logger=logger, api_key=api_key)
        fallback_names = profile.get("fallback") or []
        if not fallback_names:
            return primary
        # L4a：构造 fallback 链（一层，不递归——fallback 档案自身的 fallback 字段被忽略）
        models = config.get("models") or {}
        chain = [primary]
        seen = {profile_name}  # P6：重复项去重（冷却键同名冲突/重复撞同端点）
        for fb_name in fallback_names:
            if fb_name == profile_name:
                raise ValueError(
                    f"模型档案 '{profile_name}' 的 fallback 不能包含自身，"
                    f"请检查 config.json 的 models.{profile_name}.fallback")
            if fb_name in seen:
                if logger is not None and hasattr(logger, "warning"):
                    logger.warning(f"fallback 列表重复项 '{fb_name}'，已忽略")
                continue
            seen.add(fb_name)
            fb_profile = models.get(fb_name)
            if fb_profile is None:
                raise ValueError(
                    f"模型档案 '{profile_name}' 的 fallback 指向不存在的档案 '{fb_name}'，"
                    f"可用档案：{', '.join(models) or '（无）'}")
            try:
                # P3：fallback 档案与主档案同等校验（缺 api_url/model 时跳过并告警，
                # 而非构造出 model=None 的 client 在请求期表现为神秘失败）
                _validate_profile(fb_name, fb_profile)
                fb_model = fb_profile.get("model")
                _require_model(fb_name, fb_model)
                chain.append(_construct_profile_client(
                    config, fb_profile, fb_name, fb_model,
                    logger=logger))
            except (RuntimeError, ValueError) as e:
                # 环境问题（如 fallback 档案缺 API Key）或配置缺陷（缺 model）：
                # 跳过该 fallback 并告警，不阻断主档案可用性
                # （配置结构错误——fallback 指向不存在的档案——在上方已硬性拦截）
                if logger is not None and hasattr(logger, "warning"):
                    logger.warning(f"fallback 档案 '{fb_name}' 构造失败，已忽略：{e}")
        return FailoverClient(chain, logger=logger,
                              timeout_budget=profile.get("failover_timeout_budget"))

    if provider in ("deepseek", "deepseek-chat", "openai_compatible", "openai"):
        client = DeepSeekClient(
            api_key=resolved_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            config=config,
            logger=logger,
        )
        return client

    # 其他 OpenAI 兼容厂商：可直接复用 DeepSeekClient（继承其协议解析），
    # 或在此接入专有实现。新增厂商时在此扩展即可。
    raise ValueError(f"未知的模型提供商: {provider}")