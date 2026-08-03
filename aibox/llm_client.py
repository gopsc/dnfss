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
import requests


class LLMClient(ABC):
    """大模型客户端抽象基类。子类需实现流式/非流式两种调用，并归一化为统一事件格式。"""

    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: int, config=None, logger=None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.config = config          # 可选，用于读取代理等动态配置
        self.logger = logger          # 可选，用于日志输出
        # 当前活动的 HTTP 响应引用，供 cancel() 强制关闭底层连接以中断阻塞读取
        self._current_response = None

    @property
    def name(self) -> str:
        """厂商/模型展示名，业务层可用于上下文（如 history 里的 assistant 名）。"""
        return "LLM"

    # ---------- 工具方法 ----------

    def _resolve_proxies(self) -> Optional[Dict[str, str]]:
        """从 config 解析 requests 可用的 proxies 字典（动态代理）。"""
        if self.config is not None and hasattr(self.config, "get_proxy"):
            return self.config.get_proxy()
        return None

    def _log(self, level: str, msg: str):
        if self.logger is not None and hasattr(self.logger, level):
            try:
                getattr(self.logger, level)(msg)
            except Exception:
                pass

    def cancel(self):
        """中断当前流式请求。线程安全，可从任意线程调用。"""
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
    def chat_completion(self, messages: List[Dict], temperature: float = 0.3,
                        max_tokens: int = 1024, **kwargs) -> str:
        """非流式对话，返回完整文本。用于对话压缩等离线摘要场景。"""

    @abstractmethod
    def stream_chat(self, messages: List[Dict], temperature: float = 0.7,
                    max_tokens: int = 8192, tools: Optional[List[Dict]] = None,
                    tool_choice: Optional[str] = None,
                    reasoning_enabled: bool = False, **extra) -> Iterator[Dict]:
        """流式对话，逐条 yield 统一事件格式的 dict。业务层据此驱动输出/工具调用。"""


class DeepSeekClient(LLMClient):
    """DeepSeek 客户端（OpenAI Chat Completions 协议兼容）。

    思考模式通过 `thinking: {"type": "enabled"}` 参数控制，思考内容字段为
    `delta.reasoning_content`。二者均为 DeepSeek 特色，其他 OpenAI 兼容厂商
    若字段不同，可在子类中覆写 `_parse_delta` 归一化。
    """

    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: int = 30, config=None, logger=None):
        super().__init__(api_key, model, base_url, timeout, config, logger)

    @property
    def name(self) -> str:
        return "DeepSeek"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat_completion(self, messages: List[Dict], temperature: float = 0.3,
                        max_tokens: int = 1024, **kwargs) -> str:
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
                timeout=30,
                proxies=self._resolve_proxies(),
            )
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            self._log("error", f"LLM 压缩请求失败: HTTP {response.status_code}")
            return None
        except Exception as e:
            self._log("error", f"LLM 压缩请求异常: {e}")
            return None

    def stream_chat(self, messages: List[Dict], temperature: float = 0.7,
                    max_tokens: int = 8192, tools: Optional[List[Dict]] = None,
                    tool_choice: Optional[str] = None,
                    reasoning_enabled: bool = False, **extra) -> Iterator[Dict]:
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
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text}")

        try:
            for line in response.iter_lines():
                if not line:
                    continue
                line_text = line.decode("utf-8")
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

    def _parse_delta(self, delta: Dict, reasoning_enabled: bool) -> Iterator[Dict]:
        """将单条 delta 归一化为统一事件。子类覆写以适配不同厂商字段。"""
        # 思考内容
        if reasoning_enabled and delta.get("reasoning_content"):
            yield {"type": "reasoning", "text": delta["reasoning_content"]}
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


def resolve_api_key(api_key: Optional[str], config=None) -> str:
    """解析 API Key：优先显式传入，其次环境变量，最后 ~/.cert/deepseek.key。"""
    if api_key:
        return api_key
    env_name = None
    if config is not None and hasattr(config, "get"):
        env_name = config.get("api.api_key_env")
    env_name = env_name or "DEEPSEEK_API_KEY"
    env_key = os.environ.get(env_name)
    if env_key:
        return env_key
    try:
        home = os.environ.get("HOME", os.environ["USERPROFILE"])
        key_path = os.path.join(home, ".cert", "deepseek.key")
        with open(key_path, "r") as f:
            return f.read().strip()
    except Exception:
        raise RuntimeError(
            f"请设置环境变量 {env_name} 或在配置文件中设置 API Key")


def create_llm_client(config, api_key: Optional[str] = None,
                      logger=None) -> LLMClient:
    """根据配置创建对应厂商的 LLM 客户端。可通过 `api.provider` 切换厂商。"""
    provider = (config.get("api.provider", "deepseek") or "deepseek").lower()
    base_url = config.get("api.deepseek_api_url")
    model = config.get("api.deepseek_model", "deepseek-v4-flash")
    timeout = config.get("api.timeout", 30)

    resolved_key = resolve_api_key(api_key, config)

    if provider in ("deepseek", "deepseek-chat"):
        return DeepSeekClient(
            api_key=resolved_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            config=config,
            logger=logger,
        )

    # 其他 OpenAI 兼容厂商：可直接复用 DeepSeekClient（继承其协议解析），
    # 或在此接入专有实现。新增厂商时在此扩展即可。
    raise ValueError(f"未知的模型提供商: {provider}")