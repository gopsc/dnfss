"""轻量级 .mt 脚本 @Action 矩阵解析器

从 .mt 文件中提取 @Action 块，解析为 (n_rows, n_cols) 的 float32 矩阵。
支持 `-` 占位符（沿用上一行同列的值）。
"""

import re
from pathlib import Path

import numpy as np


def _natural_key(p: Path):
    """自然排序键，确保 s1.mt < s2.mt < s10.mt"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def parse_mt_action(path) -> np.ndarray:
    """解析 .mt 文件中的 @Action 矩阵

    Args:
        path: .mt 文件路径

    Returns:
        (n_data_rows, n_cols) float32 矩阵，表头行已跳过，`-` 已前向填充

    Raises:
        ValueError: 文件中无 @Action 块或数据行不足
    """
    text = Path(path).read_text(encoding="utf-8")
    # 先去除整行注释（# 开头），避免注释中的 @Action 干扰正则匹配
    text = re.sub(r"(?m)^[ \t]*#.*$", "", text)

    match = re.search(r"@Action\s+(.*?)\s*@", text, re.DOTALL)
    if not match:
        raise ValueError(f"未找到 @Action 块: {path}")

    block = match.group(1)

    rows = []
    for line in block.split("\n"):
        line = re.sub(r"#.*$", "", line).strip()
        if not line:
            continue
        if line.endswith(";"):
            line = line[:-1].strip()
        if not line:
            continue
        cols = re.split(r"\s+", line)
        rows.append(cols)

    if len(rows) < 2:
        raise ValueError(
            f"@Action 至少需要表头 + 1 行数据，实际 {len(rows)} 行: {path}"
        )

    # 第一行是表头（列名如 s1.A, s2.A, ...），跳过
    data_rows = rows[1:]
    n_cols = len(rows[0])

    matrix = np.zeros((len(data_rows), n_cols), dtype=np.float32)
    for i, row in enumerate(data_rows):
        for j, val in enumerate(row):
            if val == "-":
                matrix[i, j] = matrix[i - 1, j] if i > 0 else 0.0
            else:
                matrix[i, j] = float(val)

    return matrix


def parse_mt_directory(mt_dir, pattern="*.mt"):
    """解析目录下所有 .mt 文件的 @Action 矩阵

    Args:
        mt_dir: .mt 文件目录
        pattern: 文件匹配模式

    Returns:
        list of (filepath, np.ndarray) 元组，按文件名自然排序
    """
    dir_path = Path(mt_dir)
    files = sorted(dir_path.glob(pattern), key=_natural_key)
    results = []
    for f in files:
        try:
            matrix = parse_mt_action(f)
            results.append((str(f), matrix))
        except ValueError:
            pass
    return results
