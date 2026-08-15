""".mt 序列化 —— 硬件块（依赖序/声明序）+ @Action 栅格矩阵

输出契约（与 pybot parser 对齐）：
    @块名 / @ 定界；行 0 表头；行尾 ';'；列间空格分隔；
    @Action 表头为 '设备.参数' 长名（Q05 校验恰含一个 '.'），
    末列 '<speaker>.delay' 恒 10（栅格节拍），无 Speaker 块则无此列。

栅格行语义：t=10,20,…,T，行值为该 10ms 结束时刻的绝对姿态；
pybot run() 对 delay=10 的行分解为 1 个微步且插值系数 1.0，
即直接到位 —— 与栅格语义严格等价。
"""

from .timeline import STEP_MS


def fmt_num(v):
    """数值格式化：整数无小数点，小数保留至多 3 位（栅格插值值）"""
    f = float(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.3f}".rstrip("0")


def _fmt_block(lines, name, columns, rows):
    lines.append(f"@{name}")
    lines.append(" ".join(columns) + ";")
    for row in rows:
        lines.append(" ".join(str(v) for v in row) + ";")
    lines.append("@")


def emit_mt(blocks, active_channels, t_total, speaker=None):
    """生成 .mt 全文

    blocks:          [(block_name, columns, row_values_str)]（声明序）
    active_channels: [Channel]（有运动段的通道，注册序）
    t_total:         编译总时长 ms（10 的倍数）
    speaker:         delay 节拍列所属设备名；None 则无节拍列
    """
    from .dsl import CompileError     # 避免环：dsl 依赖 emit

    lines = []
    merged = []                   # 相邻同名块合并为一个块多行（.mt 惯例）
    for block_name, columns, row in blocks:
        if merged and merged[-1][0] == block_name and merged[-1][1] == columns:
            merged[-1][2].append(row)
        else:
            merged.append((block_name, columns, [row]))
    for block_name, columns, rows in merged:
        _fmt_block(lines, block_name, columns, rows)
        lines.append("")          # 块间空行（MatrixSaver 风格）

    delay_col = f"{speaker}.delay" if speaker else None
    freq_col = f"{speaker}.frequency" if speaker else None
    # pybot 契约：SpeakerVar.play 无条件读 frequency+delay 两键，
    # delay 节拍列必须与同设备 frequency 列成对出现（std.mt 惯例：
    # spk.F spk.delay；无发声需求时 frequency 恒 0，对齐 jump.mt 首行）
    active_names = {ch.name for ch in active_channels}
    fill_freq = bool(freq_col) and freq_col not in active_names

    header = [ch.name for ch in active_channels]
    if fill_freq:
        header.append(freq_col)
    if delay_col:
        header.append(delay_col)
    if not header:
        raise CompileError("@Action 无列（既无运动通道也无 Speaker 节拍列）")

    n = int(round(t_total / STEP_MS))
    rows = []
    for k in range(1, n + 1):
        t = k * STEP_MS
        row = [fmt_num(ch.value_at(t)) for ch in active_channels]
        if fill_freq:
            row.append("0")
        if delay_col:
            row.append(str(STEP_MS))
        rows.append(row)
    _fmt_block(lines, "Action", header, rows)
    return "\n".join(lines) + "\n"
