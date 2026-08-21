#!/usr/bin/env python3
"""
subsystems.conversation_compressor - 对话压缩子系统

抽离自 aibox.py，负责对话上下文压缩：消息条数或 Token 数达到阈值时，
将早期对话（含工具调用信息）经 LLM 压缩为摘要，保留最近消息。

依赖注入：构造时传入 llm_client（llm_client.LLMClient 实例）而非 AI
引擎实例，避免与 DeepSeekChat 形成循环引用；依赖方向 common <- subsystems。
"""

import re
from typing import Dict, List, Optional, Tuple

from common import constants, i18n, Logger, ConfigManager


class ConversationCompressor:
    """对话压缩管理器 - 优先压缩较早的消息，将tool调用信息一并压缩"""

    def __init__(self, llm_client=None, logger: Logger = None):
        self.llm_client = llm_client
        self.logger = logger or Logger("Compressor")
        self.config = ConfigManager()
        # 从配置获取最小值
        self.min_messages_to_compress = constants.COMPRESSOR_MIN_MESSAGES
        # 权重衰减因子：从配置获取，设为0.6使加权计算更合理
        self.weight_decay_factor = 0.6  # 使用更高的衰减因子

    def estimate_tokens(self, messages: List[Dict]) -> int:
        """估算消息的token数"""
        total_tokens = 0
        for msg in messages:
            content = msg.get("content", "")
            if content:
                chinese_chars = len([c for c in content if '\u4e00' <= c <= '\u9fff'])
                english_words = len(re.findall(r'[a-zA-Z]+', content))
                other_chars = len(content) - chinese_chars - english_words

                estimated = chinese_chars * 1.5 + english_words * 1.3 + other_chars * 0.8
                total_tokens += estimated
                total_tokens += 10

            if msg.get("tool_calls"):
                # 工具调用也计算token
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    if func.get("name"):
                        total_tokens += len(func.get("name", "")) * 1.5
                    if func.get("arguments"):
                        total_tokens += len(func.get("arguments", "")) * 0.8
                total_tokens += 50

        return int(total_tokens)

    def should_compress(self, messages: List[Dict]) -> Tuple[bool, str]:
        """检查是否需要压缩对话 - 消息条数或 Token 数任一达到阈值即压缩"""
        if not constants.COMPRESS_ENABLED:
            return False, "压缩功能已禁用"

        conversation_messages = [m for m in messages if m.get("role") != "system"]

        # 优先检查消息条数是否达到阈值
        message_count = len(conversation_messages)
        if message_count >= constants.COMPRESS_MESSAGE_THRESHOLD:
            print(f"⚠️ {i18n.get('compress_threshold_reached', message_count)}")
            return True, f"消息数({message_count})超过阈值({constants.COMPRESS_MESSAGE_THRESHOLD})"

        # 再检查 Token 数是否达到阈值（长文档/长工具输出场景）
        token_count = self.estimate_tokens(conversation_messages)
        if token_count >= constants.COMPRESS_TOKEN_THRESHOLD:
            print(f"⚠️ {i18n.get('compress_token_threshold_reached', token_count)}")
            return True, f"Token数({token_count})超过阈值({constants.COMPRESS_TOKEN_THRESHOLD})"

        return False, f"消息数({message_count})与Token数({token_count})均未达到阈值"

    def _calculate_target_count(self, current_count: int, threshold: int, keep_ratio: float = 0.7) -> int:
        """
        计算目标消息数量 - 压缩到阈值的 keep_ratio 倍

        Args:
            current_count: 当前消息数量
            threshold: 压缩阈值
            keep_ratio: 压缩后保留的比例（相对于阈值），默认0.7表示压缩到阈值的70%

        Returns:
            目标消息数量
        """
        # 压缩到阈值以下，保留阈值的 keep_ratio 倍
        target = int(threshold * keep_ratio)

        # 确保至少有 min_messages_to_compress 条消息
        target = max(target, self.min_messages_to_compress)

        # 确保不超过当前数量
        target = min(target, current_count - 1)

        return target

    def _find_compressible_block_by_count(self, messages: List[Dict], target_count: int) -> Tuple[int, int]:
        """
        根据目标消息数量找到可压缩块 - 不保护tool消息，直接压缩早期消息

        Args:
            messages: 消息列表
            target_count: 目标消息数量

        Returns:
            (start, end) 压缩块的起始和结束索引
        """
        if len(messages) <= target_count:
            return 0, 0

        # 计算需要压缩的消息数量
        messages_to_remove = len(messages) - target_count

        # 确定要保留的最近消息数量
        keep_recent = constants.COMPRESS_KEEP_RECENT
        if keep_recent > 0:
            keep_recent = max(keep_recent, 10)  # 至少保留最近10条

        # 压缩块应该从最早的消息开始，但需要保留前几轮对话
        # 计算最早可以开始压缩的位置（跳过前2轮对话）
        start_min = 0

        # 尝试找到前2个对话轮次结束的位置
        conversation_turns = self._build_conversation_turns(messages)
        if len(conversation_turns) >= 2:
            # 前2轮结束的位置
            first_turn = conversation_turns[0]
            second_turn = conversation_turns[1]
            # 取第二轮结束的位置
            if second_turn["assistant_index"] is not None:
                start_min = second_turn["assistant_index"] + 1
            else:
                start_min = second_turn["user_index"] + 1

        # 压缩结束的位置 = 总消息数 - 保留的最近消息数
        end_max = len(messages) - keep_recent

        # 确保压缩块有效
        if end_max <= start_min + 2:
            # 如果保护区域太大，尝试压缩更多
            end_max = len(messages) - max(keep_recent // 2, 5)

        # 计算需要压缩的块大小
        block_size = min(messages_to_remove + 10, end_max - start_min)
        block_size = max(block_size, 10)  # 至少压缩10条

        # 压缩块从 start_min 开始
        start = start_min
        end = min(start + block_size, end_max)

        # 确保压缩块至少2条消息
        if end - start < 2:
            # 如果块太小，尝试从更早的位置开始
            start = max(0, end_max - block_size)
            end = end_max

        self.logger.debug(f"找到压缩块: [{start}:{end}], 移除{end-start}条消息")
        return start, end

    def _build_conversation_turns(self, messages: List[Dict]) -> List[Dict]:
        """构建对话轮次"""
        conversation_turns = []

        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role")

            # 跳过系统消息
            if role == "system":
                i += 1
                continue

            # 遇到用户消息，开始一个新的轮次
            if role == "user":
                turn = {"user_index": i, "assistant_index": None, "tool_indices": []}

                # 向后查找对应的助手消息和工具消息
                j = i + 1
                while j < len(messages):
                    next_msg = messages[j]
                    next_role = next_msg.get("role")

                    # 跳过系统消息
                    if next_role == "system":
                        j += 1
                        continue

                    # 找到助手消息
                    if next_role == "assistant":
                        turn["assistant_index"] = j
                        # 检查这个助手消息是否有 tool_calls
                        if next_msg.get("tool_calls"):
                            # 收集这个助手消息的 tool_call_ids
                            tool_call_ids = {tc.get("id") for tc in next_msg.get("tool_calls", []) if tc.get("id")}
                            # 继续查找对应的 tool 消息
                            k = j + 1
                            while k < len(messages) and len(turn["tool_indices"]) < len(tool_call_ids):
                                tool_msg = messages[k]
                                if tool_msg.get("role") == "tool":
                                    tool_call_id = tool_msg.get("tool_call_id")
                                    if tool_call_id and tool_call_id in tool_call_ids:
                                        turn["tool_indices"].append(k)
                                    k += 1
                                elif tool_msg.get("role") == "user":
                                    break
                                else:
                                    k += 1
                        break

                    if next_role == "user":
                        break

                    j += 1

                conversation_turns.append(turn)
                i += 1
            else:
                i += 1

        return conversation_turns

    def _build_compression_prompt(self, messages: List[Dict]) -> str:
        """构建压缩提示词 - 包含工具调用信息"""
        conversation_lines = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "user":
                if content:
                    conversation_lines.append(f"用户: {content}")

            elif role == "assistant":
                if content:
                    conversation_lines.append(f"助手: {content}")

                # 包含工具调用信息
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    arguments = func.get("arguments", "")
                    if tool_name:
                        if arguments:
                            conversation_lines.append(f"[调用工具: {tool_name}({arguments[:100]})]")
                        else:
                            conversation_lines.append(f"[调用工具: {tool_name}]")

            elif role == "tool":
                if content:
                    useful = self._extract_useful_content(content)
                    if useful:
                        conversation_lines.append(f"[工具结果: {useful}]")

        conversation_text = "\n".join(conversation_lines)

        max_input_chars = constants.COMPRESSOR_MAX_INPUT_CHARS
        if len(conversation_text) > max_input_chars:
            conversation_text = conversation_text[:max_input_chars] + "\n...(内容过长，已截断)..."

        prompt = f"""请对以下对话内容进行智能压缩和总结。要求：

1. **提取核心信息**：对话的主要主题、关键问题、重要结论、用户需求
2. **保留重要细节**：用户的重要要求、AI的重要回复、达成的共识
3. **保留工具调用摘要**：简要说明使用了什么工具，得到了什么关键结果
4. **结构化输出**：使用清晰的段落结构

对话内容：
---
{conversation_text}
---

请输出压缩后的对话摘要："""

        return prompt

    def _extract_useful_content(self, content: str) -> str:
        """从工具返回内容中提取有用信息"""
        if not content:
            return ""

        if "✅" in content:
            lines = content.split("\n")
            for line in lines[:3]:
                if "✅" in line:
                    return line.strip()

        if "❌" in content:
            lines = content.split("\n")
            for line in lines[:2]:
                if "❌" in line:
                    return line.strip()

        if len(content) > 100:
            return content[:100] + "..."

        return content

    def _compress_via_llm(self, messages: List[Dict]) -> Optional[str]:
        """通过语言模型压缩消息（经由注入的客户端抽象层调用，支持多厂商）"""
        if not messages or not self.llm_client:
            return None

        prompt = self._build_compression_prompt(messages)

        print(f"📦 {i18n.get('conversation_compress')}: 正在压缩 {len(messages)} 条消息...")

        try:
            summary = self.llm_client.chat_completion(
                [
                    {"role": "system", "content": "你是一个专业的对话摘要助手，擅长提取对话核心信息并进行结构化总结。请保留工具调用的关键结果。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=constants.COMPRESSOR_LLM_MAX_TOKENS
            )
            return summary
        except Exception as e:
            self.logger.error(f"语言模型压缩异常: {e}")
            return None

    def _validate_message_structure(self, messages: List[Dict]) -> List[Dict]:
        """验证并修复消息结构（tool 配对双向完整性）。

        OpenAI 协议要求 assistant.tool_calls 与对应的 tool 结果消息成对出现，
        缺任一侧后续 API 调用直接 400：
        1. 孤立 tool 消息（tool_call_id 无对应 assistant）→ 移除（既有行为）
        2. T23（审计 2026-08-21）：压缩块边界按条数切，可能落在带 tool_calls
           的 assistant 消息与其 tool 结果之间——保留侧 assistant 的
           tool_calls 失去配对 → 剥离失配的 tool_calls 条目；全部失配时删除
           字段（content 为空则置空串，防产生非法空消息）
        """
        if not messages:
            return messages

        # tool 侧实际存在的 id
        present_tool_ids = {
            m.get("tool_call_id")
            for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }

        # 第一遍：assistant 剥离失去配对的 tool_calls
        first_pass = []
        kept_assistant_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                calls = msg.get("tool_calls", [])
                kept_calls = [tc for tc in calls
                              if tc.get("id") and tc.get("id") in present_tool_ids]
                kept_assistant_ids.update(tc.get("id") for tc in kept_calls)
                if len(kept_calls) == len(calls):
                    first_pass.append(msg)  # 全部有配对，原样保留
                    continue
                new_msg = dict(msg)
                if kept_calls:
                    new_msg["tool_calls"] = kept_calls
                    print(f"   ⚠️ 剥离部分失去配对 tool 结果的 tool_calls（T23）")
                else:
                    new_msg.pop("tool_calls", None)
                    if not new_msg.get("content"):
                        new_msg["content"] = ""
                    print(f"   ⚠️ 剥离失去配对 tool 结果的 assistant.tool_calls（T23）")
                first_pass.append(new_msg)
            else:
                first_pass.append(msg)

        # 第二遍：移除孤立 tool 消息（其 id 不在任何保留的 assistant tool_calls 中）
        filtered = []
        for msg in first_pass:
            if msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id and tool_call_id not in kept_assistant_ids:
                    print(f"   ⚠️ 移除孤立的 tool 消息: {tool_call_id}")
                    continue
            filtered.append(msg)

        return filtered

    def compress_conversation(self, messages: List[Dict]) -> List[Dict]:
        """
        压缩对话内容 - 优先压缩较早的消息，包含tool调用信息一并压缩
        当消息条数或 Token 数超过阈值时，压缩到阈值以下
        """
        if not messages:
            return messages

        # 分离系统消息和对话消息
        system_messages = []
        conversation_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_messages.append(msg)
            else:
                conversation_messages.append(msg)

        if not conversation_messages:
            return system_messages

        original_count = len(conversation_messages)
        original_tokens = self.estimate_tokens(conversation_messages)
        threshold = constants.COMPRESS_MESSAGE_THRESHOLD

        print(f"\n📊 对话压缩检查:")
        print(f"   当前消息数: {original_count} 条 (阈值: {threshold} 条)")
        print(f"   当前Token数: {original_tokens} 个")

        # 计算目标消息数量 - 压缩到阈值的70%
        target_count = self._calculate_target_count(original_count, threshold, keep_ratio=0.7)

        if original_count < threshold:
            # 消息数未超阈值（由 Token 阈值触发）：按比例压缩早期消息
            target_count = max(self.min_messages_to_compress, original_count // 2)
            print(f"   Token 触发压缩：目标消息数改为 {target_count} 条 (当前的一半)")
        else:
            print(f"   目标消息数: {target_count} 条 (压缩后应少于阈值)")

        # 如果目标数量大于等于当前数量，不需要压缩
        if target_count >= original_count:
            print(f"   目标消息数大于等于当前，跳过压缩")
            return messages

        # 计算需要压缩的消息数量
        messages_to_compress = original_count - target_count
        print(f"   需要压缩的消息数量: {messages_to_compress} 条")

        # 根据消息数量查找可压缩块
        compress_start, compress_end = self._find_compressible_block_by_count(
            conversation_messages, target_count
        )

        if compress_end - compress_start >= 2:
            compressible = conversation_messages[compress_start:compress_end]
            compress_tokens = self.estimate_tokens(compressible)
            compress_count = len(compressible)

            print(f"   压缩块范围: [{compress_start}:{compress_end}], 共 {compress_count} 条消息")
            print(f"   压缩块token数: {compress_tokens}")

            # 如果压缩块大小仍然不够，尝试扩大压缩块
            if compress_count < messages_to_compress:
                print(f"   压缩块大小不足({compress_count} < {messages_to_compress})，扩大压缩块...")
                # 扩大压缩块
                additional = messages_to_compress - compress_count + 5
                new_start = max(0, compress_start - additional // 2)
                new_end = min(len(conversation_messages), compress_end + additional // 2)
                compress_start, compress_end = new_start, new_end
                compressible = conversation_messages[compress_start:compress_end]
                compress_count = len(compressible)
                print(f"   扩大后压缩块: [{compress_start}:{compress_end}], 共 {compress_count} 条消息")
        else:
            print(f"   未找到合适的压缩块，使用默认压缩策略")
            # 使用默认压缩策略：压缩最早的一半消息，保留最近的消息
            keep_recent = constants.COMPRESS_KEEP_RECENT
            compress_end = len(conversation_messages) - keep_recent
            compress_start = max(0, compress_end - messages_to_compress - 5)
            compress_start = max(compress_start, 0)
            compressible = conversation_messages[compress_start:compress_end]
            compress_count = len(compressible)
            print(f"   默认压缩块: [{compress_start}:{compress_end}], 共 {compress_count} 条消息")

        # 确保压缩块有足够多的消息
        if compress_count < 5:
            print(f"   压缩块太小({compress_count}条)，跳过压缩")
            return messages

        # 调用语言模型压缩
        summary = self._compress_via_llm(compressible)

        if not summary:
            print(f"   ❌ 压缩失败，保留原始对话")
            return messages

        # 构建压缩后的消息列表
        compressed_result = []

        # 添加压缩块之前的消息
        compressed_result.extend(conversation_messages[:compress_start])

        # 插入压缩摘要（作为系统消息）
        compressed_result.append({
            "role": "system",
            "content": i18n.get('compressed_summary', summary)
        })

        # 添加压缩块之后的消息（包括最近的消息）
        compressed_result.extend(conversation_messages[compress_end:])

        # 验证并修复消息结构
        compressed_result = self._validate_message_structure(compressed_result)

        # 合并系统消息
        final_messages = system_messages + compressed_result

        # 统计结果
        final_count = len([m for m in final_messages if m.get("role") != "system"])
        final_tokens = self.estimate_tokens([m for m in final_messages if m.get("role") != "system"])

        print(f"\n✅ {i18n.get('conversation_compressed', original_count, final_count)}")
        # L6：压缩块全空时 original_tokens 可能为 0，除零防护
        print(f"   Token: {original_tokens} -> {final_tokens} (压缩率: {final_tokens / max(original_tokens, 1):.1%})")

        # 检查是否压缩到阈值以下
        if final_count >= threshold:
            print(f"   ⚠️ 警告: 压缩后消息数({final_count})仍大于等于阈值({threshold})")

        return final_messages
