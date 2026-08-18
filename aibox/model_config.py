#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大模型接入点（模型档案）交互式管理工具

不用手写 config.json：菜单式列出 / 添加 / 删除 / 连通性验证
`models` 节的模型接入点（OpenAI Chat Completions 兼容端点：
DeepSeek / Kimi / Qwen / GLM / 本地 Ollama 等）。

用法：
  python model_config.py          # 交互式菜单
  python model_config.py --list   # 非交互：列出当前接入点

安全约定（与 aibox 审计一致）：API Key 一律不写入配置文件——
只保存环境变量名（api_key_env），或留空表示免密本地端点。
"""

import argparse
import os
import re
import sys
from typing import Dict, List, Optional

# 允许从仓库根目录或 aibox/ 目录启动
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import ConfigManager  # noqa: E402

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 常见厂商端点提示（首行是默认建议值）
PROVIDER_HINTS = {
    "DeepSeek": "https://api.deepseek.com/v1/chat/completions",
    "Kimi (Moonshot)": "https://api.moonshot.cn/v1/chat/completions",
    "Qwen (阿里云百炼)": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "GLM (智谱)": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "Ollama 本地": "http://localhost:11434/v1/chat/completions",
}


# ==================== 核心逻辑（可单测，不依赖 input） ====================

def get_models(config: ConfigManager) -> Dict:
    """取 models 节（不存在则初始化为空 dict）。"""
    models = config.get("models") or {}
    if not isinstance(models, dict):
        models = {}
    return models


def list_profiles(config: ConfigManager) -> List[Dict]:
    """列出所有档案的归一化展示数据。"""
    models = get_models(config)
    default = config.get("api.default_model") or ""
    profiles = []
    for name, profile in models.items():
        if not isinstance(profile, dict):
            continue
        profiles.append({
            "name": name,
            "is_default": name == default,
            "display_name": profile.get("display_name") or name,
            "api_url": profile.get("api_url", ""),
            "model": profile.get("model", ""),
            "api_key_env": profile.get("api_key_env", ""),
            "reasoning_enabled": bool(profile.get("reasoning_enabled", False)),
            "temperature": profile.get("temperature"),
            "timeout": profile.get("timeout"),
            "fallback": profile.get("fallback") or [],
        })
    return profiles


def validate_new_profile(config: ConfigManager, name: str, api_url: str,
                         model: str) -> Optional[str]:
    """校验新档案字段，返回错误信息（合法返回 None）。"""
    if not name:
        return "档案名不能为空"
    if not PROFILE_NAME_RE.match(name):
        return f"档案名只能包含字母/数字/下划线/短横线（当前: {name}）"
    models = get_models(config)
    if name in models:
        return f"档案名 '{name}' 已存在"
    if not api_url or not str(api_url).strip().lower().startswith(("http://", "https://")):
        return "API 地址必须是 http:// 或 https:// 开头的完整 URL"
    if not model or not str(model).strip():
        return "模型名不能为空（如 deepseek-chat / gpt-4o / qwen-plus）"
    return None


def add_profile(config: ConfigManager, name: str, api_url: str, model: str,
                api_key_env: str = "", display_name: str = None,
                temperature: float = None, timeout: int = None,
                reasoning_enabled: bool = False,
                fallback: Optional[List[str]] = None,
                set_default: bool = False) -> List[str]:
    """添加档案。返回提示信息列表；字段非法时返回带 ❌ 的错误信息。"""
    err = validate_new_profile(config, name, api_url, model)
    if err:
        return [f"❌ {err}"]

    models = get_models(config)
    profile = {
        "api_url": str(api_url).strip(),
        "model": str(model).strip(),
    }
    if api_key_env:
        profile["api_key_env"] = api_key_env.strip()
    else:
        # 空串 = 免密本地端点（Ollama/vLLM），显式声明
        profile["api_key_env"] = ""
    if display_name:
        profile["display_name"] = display_name.strip()
    if temperature is not None:
        try:
            profile["temperature"] = max(0.0, min(2.0, float(temperature)))
        except (TypeError, ValueError):
            return ["❌ 温度必须是 0.0~2.0 的数字"]
    if timeout is not None:
        try:
            profile["timeout"] = max(5, int(timeout))
        except (TypeError, ValueError):
            return ["❌ 超时必须是整数秒"]
    if reasoning_enabled:
        profile["reasoning_enabled"] = True
    if fallback:
        # 只保留已存在的档案，避免悬空引用（L4a 启动期硬性报错）
        known = set(models.keys())
        invalid = [f for f in fallback if f not in known or f == name]
        if invalid:
            return [f"❌ 失败转移包含不存在的档案或自身: {invalid}，请重新选择"]
        profile["fallback"] = list(fallback)

    models[name] = profile
    config._config["models"] = models
    if set_default:
        config._config.setdefault("api", {})["default_model"] = name

    saved = config._save_config()
    msgs = [f"✅ 接入点 '{name}' 已添加（{profile['api_url']}，模型 {profile['model']}）"]
    if not saved:
        msgs.append("⚠️ 保存失败（仅本次运行生效，重启后丢失）——请检查配置文件目录权限")
    if set_default:
        msgs.append(f"⭐ 已设为默认接入点（--model 不指定时使用）")
    if not api_key_env:
        msgs.append("ℹ️ 免密端点：请求不携带 API Key（适用于 Ollama/vLLM 等本地服务）")
    else:
        msgs.append(f"🔑 Key 从环境变量 {api_key_env} 读取（未设置时该档案不可用）")
    return msgs


def delete_profile(config: ConfigManager, name: str) -> List[str]:
    """删除档案，并自动清理其他档案 fallback 中的悬空引用。"""
    models = get_models(config)
    if name not in models:
        return [f"❌ 档案 '{name}' 不存在"]

    del models[name]

    # 清理其他档案对该档案的 fallback 引用（悬空引用会导致启动期报错）
    cleaned = []
    for other, profile in models.items():
        if not isinstance(profile, dict):
            continue
        fb = profile.get("fallback") or []
        if name in fb:
            profile["fallback"] = [f for f in fb if f != name]
            cleaned.append(other)
    config._config["models"] = models

    # 若删除的是默认档案，清除默认标记（回退旧 api 节语义）
    if config.get("api.default_model") == name:
        config._config.setdefault("api", {}).pop("default_model", None)

    saved = config._save_config()
    msgs = [f"✅ 接入点 '{name}' 已删除"]
    if cleaned:
        msgs.append(f"ℹ️ 已同时清理 {len(cleaned)} 个档案的失败转移引用: {', '.join(cleaned)}")
    if not saved:
        msgs.append("⚠️ 保存失败——请检查配置文件目录权限")
    return msgs


def test_profile(config: ConfigManager, name: str, logger=None) -> str:
    """连通性验证：向该档案端点发一次最小请求。"""
    models = get_models(config)
    if name not in models:
        return f"❌ 档案 '{name}' 不存在"
    try:
        from llm_client import create_llm_client
        client = create_llm_client(config, api_key=None, logger=logger, model=name)
        result = client.chat_completion(
            [{"role": "user", "content": "ping"}], max_tokens=5)
        if result is None or not str(result).strip():
            return f"❌ 档案 '{name}' 验证失败：端点返回空响应（多为 4xx 认证/参数错误）"
        return f"✅ 档案 '{name}' 连通正常：{str(result).strip()[:60]}"
    except Exception as e:
        return f"❌ 档案 '{name}' 验证失败：{str(e)}"


# ==================== 交互式菜单 ====================

def _ask(prompt: str, default: str = "") -> str:
    if default:
        val = input(f"{prompt}（回车默认: {default}）: ").strip()
        return val if val else default
    return input(f"{prompt}: ").strip()


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    hint = "y/n" if not default else "Y/n"
    while True:
        val = input(f"{prompt} [{hint}] ").strip().lower()
        if not val:
            return default
        if val in ("y", "yes", "是"):
            return True
        if val in ("n", "no", "否"):
            return False
        print("  请输入 y 或 n")


def _choose(prompt: str, options: List[str], multi: bool = False) -> List[str]:
    """编号选择（单选或多选）。多选用逗号分隔，直接回车 = 不选。"""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    if multi:
        while True:
            raw = input(f"{prompt}（多选，逗号分隔，直接回车不选）: ").strip()
            if not raw:
                return []
            try:
                idxs = [int(x) for x in raw.replace("，", ",").split(",") if x.strip()]
            except ValueError:
                print("  请输入数字编号，逗号分隔")
                continue
            if all(1 <= i <= len(options) for i in idxs):
                return [options[i - 1] for i in idxs]
            print(f"  编号超出范围（1~{len(options)}）")
    else:
        while True:
            raw = input(f"{prompt}（1~{len(options)}，直接回车不选）: ").strip()
            if not raw:
                return []
            try:
                idx = int(raw)
            except ValueError:
                print("  请输入数字编号")
                continue
            if 1 <= idx <= len(options):
                return [options[idx - 1]]
            print(f"  编号超出范围（1~{len(options)}）")


def _print_profiles(profiles: List[Dict]):
    if not profiles:
        print("  （当前没有任何接入点。旧版 api 节仍可用，或选 2 添加）")
        return
    for p in profiles:
        mark = " ⭐默认" if p["is_default"] else ""
        key_desc = f"环境变量 {p['api_key_env']}" if p["api_key_env"] else "免密"
        fb = f"，失败转移 → {', '.join(p['fallback'])}" if p["fallback"] else ""
        print(f"  {p['name']}{mark}  [{p['display_name']}]")
        print(f"      地址: {p['api_url']}   模型: {p['model']}")
        print(f"      Key: {key_desc}   温度: {p['temperature'] or '默认'}   超时: {p['timeout'] or '默认'}{fb}")


def _add_flow(config: ConfigManager):
    print("\n── 添加接入点 ──")
    print("常见厂商端点参考：")
    for k, v in PROVIDER_HINTS.items():
        print(f"  {k}: {v}")
    name = _ask("档案名（英文，如 deepseek/kimi/local）")
    if not name:
        print("❌ 已取消（档案名不能为空）")
        return
    if not PROFILE_NAME_RE.match(name):
        print(f"❌ 档案名只能包含字母/数字/下划线/短横线")
        return
    if name in get_models(config):
        print(f"❌ 档案名 '{name}' 已存在")
        return
    api_url = _ask("API 地址")
    model = _ask("模型名（如 deepseek-chat / qwen-plus）")

    print("Key 方式：")
    print("  1. 环境变量名（推荐，如 DEEPSEEK_API_KEY——Key 不落盘，重启不丢）")
    print("  2. 免密本地端点（如 Ollama，不需要 Key）")
    key_mode = _ask("选择 [1/2]", "1")
    api_key_env = ""
    if key_mode == "1":
        api_key_env = _ask("环境变量名", "DEEPSEEK_API_KEY")
    elif key_mode != "2":
        print("❌ 已取消（Key 方式无效）")
        return

    display_name = _ask("显示名（可选，回车用档案名）")
    temperature_raw = _ask("温度 0.0~2.0（可选，越低越确定）")
    timeout_raw = _ask("超时秒数（可选）")
    reasoning = _ask_yes_no("启用思考模式？", default=False)

    profiles = list_profiles(config)
    fallback = []
    if profiles:
        print("失败转移：主接入点故障时按顺序尝试的备用接入点（可选）")
        fallback = _choose("选择备用接入点", [p["name"] for p in profiles], multi=True)

    set_default = _ask_yes_no("设为默认接入点？", default=(len(profiles) == 0))

    print("\n── 确认 ──")
    print(f"  档案名: {name}")
    print(f"  地址: {api_url}   模型: {model}")
    print(f"  Key: {'环境变量 ' + api_key_env if api_key_env else '免密（不携带）'}")
    if display_name:
        print(f"  显示名: {display_name}")
    if temperature_raw:
        print(f"  温度: {temperature_raw}")
    if timeout_raw:
        print(f"  超时: {timeout_raw}s")
    if reasoning:
        print("  思考模式: 开启")
    if fallback:
        print(f"  失败转移: {', '.join(fallback)}")
    if not _ask_yes_no("确认保存？", default=True):
        print("已取消")
        return

    msgs = add_profile(config, name, api_url, model,
                       api_key_env=api_key_env, display_name=display_name,
                       temperature=float(temperature_raw) if temperature_raw else None,
                       timeout=int(timeout_raw) if timeout_raw else None,
                       reasoning_enabled=reasoning, fallback=fallback,
                       set_default=set_default)
    for m in msgs:
        print(m)


def _delete_flow(config: ConfigManager):
    profiles = list_profiles(config)
    if not profiles:
        print("\n（当前没有任何接入点）")
        return
    print("\n── 删除接入点 ──")
    picked = _choose("选择要删除的接入点", [p["name"] for p in profiles])
    if not picked:
        print("已取消")
        return
    name = picked[0]
    if not _ask_yes_no(f"确认删除 '{name}'？（其他档案的失败转移引用会自动清理）", default=False):
        print("已取消")
        return
    for m in delete_profile(config, name):
        print(m)


def _test_flow(config: ConfigManager):
    profiles = list_profiles(config)
    if not profiles:
        print("\n（当前没有任何接入点）")
        return
    print("\n── 连通性验证 ──")
    picked = _choose("选择要验证的接入点", [p["name"] for p in profiles])
    if not picked:
        print("已取消")
        return
    print("  正在请求端点（最长 20 秒）...")
    print(test_profile(config, picked[0]))


def run_interactive(config: ConfigManager = None):
    """交互式主循环。config 可注入（测试用）。"""
    config = config or ConfigManager()
    config_path = getattr(config, "_config_path", None) or "（默认位置）"
    print("=" * 56)
    print("  aibox 大模型接入点管理工具")
    print(f"  配置文件: {config_path}")
    print("  说明：API Key 只存环境变量名，不落盘；免密端点适合 Ollama 等本地服务")
    print("=" * 56)
    while True:
        print("\n请选择操作：")
        print("  1. 📋 列出接入点")
        print("  2. ➕ 添加接入点")
        print("  3. 🗑️ 删除接入点")
        print("  4. 🔍 验证接入点连通性")
        print("  0. 退出")
        choice = input("> ").strip()
        if choice == "1":
            _print_profiles(list_profiles(config))
        elif choice == "2":
            _add_flow(config)
        elif choice == "3":
            _delete_flow(config)
        elif choice == "4":
            _test_flow(config)
        elif choice in ("0", "q", "exit", "退出"):
            print("再见")
            return
        else:
            print("无效选择，请输入 0~4")


def main():
    parser = argparse.ArgumentParser(description="aibox 大模型接入点管理工具")
    parser.add_argument("--list", action="store_true", help="非交互：列出当前接入点")
    args = parser.parse_args()

    config = ConfigManager()
    if args.list:
        _print_profiles(list_profiles(config))
        return
    run_interactive(config)


if __name__ == "__main__":
    main()
