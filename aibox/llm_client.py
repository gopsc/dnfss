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
                 timeout: int, config=None, logger=None,
                 reasoning_enabled: bool = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.config = config          # 可选，用于读取代理等动态配置
        self.logger = logger          # 可选，用于日志输出
        # M3：档案级思考模式开关为构造契约；None = 未配置（消费方回退全局 api.reasoning_enabled）
        self.reasoning_enabled = reasoning_enabled
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
                 timeout: int = 30, config=None, logger=None,
                 reasoning_enabled: bool = None):
        super().__init__(api_key, model, base_url, timeout, config, logger,
                         reasoning_enabled=reasoning_enabled)

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
                timeout=self.timeout,
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
        home = os.environ.get("HOME", os.environ["USERPROFILE"])
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


def _require_model(name: str, effective_model) -> None:
    """H2：有效模型名校验——缺 model 会把档案名当模型名发给 API（或 "model": null → 400）。"""
    if not str(effective_model or "").strip():
        raise ValueError(
            f"模型档案 '{name}' 缺少 model 字段（且未传入裸模型名），"
            f"请在 config.json 的 models.{name} 中补齐，或调用时用 --model 指定模型名")


def _resolve_model_profile(config, model: Optional[str]):
    """解析模型档案。返回 (profile_dict, effective_model_name)。

    优先级：model 命中 models.<name> 档案 > api.default_model 档案 > None（回退旧 api 节）。
    model 未命中档案时视为裸模型名（同端点换模型，用默认档案的 url/key，模型名用裸名）。
    """
    if config is None or not hasattr(config, "get"):
        return None, model
    models = config.get("models") or {}
    if not isinstance(models, dict):
        return None, model

    explicit = models.get(model) if model else None
    default_name = config.get("api.default_model")
    default_profile = models.get(default_name) if default_name else None

    if explicit is not None:
        # model 是档案名 → 完整采用该档案（url/key/模型名）；
        # 档案缺 model 属配置错误，直接报错（不再把档案名当模型名发出）
        _validate_profile(model, explicit)
        effective = explicit.get("model")
        _require_model(model, effective)
        return explicit, effective
    if default_profile is not None:
        # model 是裸名或未指定 → 默认档案的 url/key，模型名被裸名覆盖
        _validate_profile(default_name, default_profile)
        effective = model or default_profile.get("model")
        _require_model(default_name, effective)
        return default_profile, effective
    return None, model


def create_llm_client(config, api_key: Optional[str] = None,
                      logger=None, model: Optional[str] = None) -> LLMClient:
    """根据配置创建对应厂商的 LLM 客户端。

    模型选择优先级：
      1. model 命中 models 节档案名 → 采用该档案（api_url/model/api_key_env/timeout）
      2. api.default_model 指向的档案 → model 为裸模型名时覆盖档案内模型名
      3. 均未配置 → 回退旧 api 扁平节（deepseek_api_url/deepseek_model）

    provider: deepseek / openai_compatible / openai 均映射到 DeepSeekClient
    （OpenAI Chat Completions 协议兼容实现，厂商差异由子类覆写 _parse_delta）。

    Args:
        config: 配置对象（需提供 get 方法）。
        api_key: 显式传入的 API Key；为 None 时按 resolve_api_key 解析。
        logger: 可选日志对象。
        model: 档案名或裸模型名。
    """
    profile, model = _resolve_model_profile(config, model)

    env_name = None
    allow_missing = False
    if profile is not None:
        base_url = profile.get("api_url")
        timeout = profile.get("timeout") or config.get("api.timeout", 30)
        env_name = profile.get("api_key_env")
        if env_name == "":
            allow_missing = True  # 档案显式空串 → 本地免密端点
    else:
        provider = (config.get("api.provider", "deepseek") or "deepseek").lower()
        base_url = config.get("api.deepseek_api_url")
        model = model or config.get("api.deepseek_model", "deepseek-v4-flash")
        timeout = config.get("api.timeout", 30)

    resolved_key = resolve_api_key(api_key, config, env_name=env_name,
                                   allow_missing=allow_missing,
                                   allow_key_file=profile is None)

    if profile is not None:
        provider = "openai_compatible"
    if provider in ("deepseek", "deepseek-chat", "openai_compatible", "openai"):
        client = DeepSeekClient(
            api_key=resolved_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            config=config,
            logger=logger,
            # M3：档案级思考模式作为构造参数（None=未配置，消费方回退全局），
            # 不再构造后挂实例属性——契约显式化，杜绝外部误设属性静默覆盖全局
            reasoning_enabled=(profile.get("reasoning_enabled",
                                           config.get("api.reasoning_enabled", False))
                               if profile is not None else None),
        )
        return client

    # 其他 OpenAI 兼容厂商：可直接复用 DeepSeekClient（继承其协议解析），
    # 或在此接入专有实现。新增厂商时在此扩展即可。
    raise ValueError(f"未知的模型提供商: {provider}")