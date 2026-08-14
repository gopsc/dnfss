"""actpred 共享工具函数（无 torch 依赖，推理/部署文件可复用）"""

import numpy as np


def parse_action_history(s: str, action_dim: int) -> np.ndarray:
    """解析冒号分隔的动作历史字符串为 (H, D) float32 数组"""
    steps = s.split(":")
    history = []
    for step in steps:
        values = [float(v) for v in step.split(",")]
        if len(values) != action_dim:
            raise ValueError(
                f"Expected {action_dim} values per step, got {len(values)}")
        history.append(values)
    return np.array(history, dtype=np.float32)