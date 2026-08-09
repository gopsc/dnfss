"""矩阵脚本（.mt）解析器 —— 移植自 bot4cj/src/P.cj

与仓颉版的差异（均为审计修复，语义不变）：
    S01: submit_line 的列数校验修复为真实生效（仓颉版因 `!` 优先级恒假）；
    S02: 移除逐字符 debug 打印，stdout 保持纯净，诊断走 logging(stderr)；
    S04: 换行统一用 '\\n' 字符比较（Python 无 Rune/String 混淆问题）；
    Q05: 越界访问抛 MatrixIndexError（携带块名/行/列），报错可读。
"""

import logging

log = logging.getLogger("pybot.parser")


# ---------------- 解析异常 ----------------

class ParseError(Exception):
    """解析异常基类（对应仓颉版 ParseExcept）"""

    def __init__(self, msg, line):
        super().__init__(f"{msg}. we got line: {line}")
        self.line = line


class DoesNotSync(ParseError):
    """行没对齐：某一行的列数与上一行不一致（修复 S01，携带期望/实际列数）"""

    def __init__(self, line, expected, actual):
        super().__init__(
            f"Matrix line does not sync (expected {expected} columns, got {actual})",
            line)
        self.expected = expected
        self.actual = actual


class MatrixIndexError(Exception):
    """矩阵越界访问（修复 Q05，携带块名/行/列）"""

    def __init__(self, name, row, col):
        super().__init__(f"matrix @{name}: cell ({row}, {col}) out of range")
        self.name = name
        self.row = row
        self.col = col


# ---------------- 矩阵编程语言 - 基本对象 ----------------

class MatrixObject:
    """一个 @块（名字 + 原始字符串矩阵）"""

    def __init__(self):
        self.name = ""      # 块名（@ 后面的标识符）
        self.values = []    # 原始矩阵 list[list[str]]（含标题行）
        self._tmp = []      # rune buf 字符缓冲区
        self._grp = []      # one line 整行缓冲区

    # -------- 是否已命名 --------
    def has_name(self):
        return self.name != ""

    # -------- 行数（含标题行）/ 列数 --------
    @property
    def size(self):
        return len(self.values)

    @property
    def col(self):
        return len(self.values[0]) if self.values else 0

    # -------- 获取矩阵元素，只能取，不能修改 --------
    def __getitem__(self, rc):
        row, col = rc
        try:
            return self.values[row][col]
        except IndexError:
            raise MatrixIndexError(self.name, row, col) from None

    # -------- 在某一行中寻找某一个字符串的列号 --------
    def index_of_col(self, row, key):
        for c, item in enumerate(self.values[row]):
            if item == key:
                return c
        raise KeyError(f"column '{key}' not found in matrix @{self.name}")

    # -------- 储存对象（与仓颉版 MatrixObject.save 输出格式一致） --------
    def save(self, out):
        out.write(f"@{self.name}\n")
        for vec in self.values:
            for item in vec:
                out.write(item)
                out.write(" ")
            out.write(";\n")
        out.write("@\n")

    # -------- FSM 提交接口（由 MatrixParser 驱动） --------
    def add_rune(self, c):
        self._tmp.append(c)

    def submit_name(self):
        if not self._tmp:
            return
        self.name = "".join(self._tmp)
        self._tmp.clear()

    def submit_item(self):
        if not self._tmp:
            return
        self._grp.append("".join(self._tmp))
        self._tmp = []

    def submit_line(self, count):
        if not self._grp:
            return
        # 修复 S01：仓颉版 `!values.size == 0 && !grp.size == ...` 因优先级恒假
        if self.values and len(self._grp) != len(self.values[-1]):
            raise DoesNotSync(count, len(self.values[-1]), len(self._grp))
        self.values.append(self._grp)
        self._grp = []


# ---------------- 矩阵式解析器（FSM 逐字符，状态与仓颉版一致） ----------------

class MatrixParser:
    """输入文本流，逐块产出 MatrixObject（一次 parse() 解析一个 @块）"""

    def __init__(self, source):
        self.reset(source)

    def reset(self, source):
        """切换目标输入；source 为 str 或带 read() 的文本流"""
        self._text = source if isinstance(source, str) else source.read()
        self._pos = 0
        self._flag = False   # 结束标志
        self._nl = True      # new line
        self._cm = False     # comment
        self._nm = False     # name-line
        self.obj = MatrixObject()

    def parse(self):
        """解析下一个 @块；输入耗尽且无新块时返回 None"""
        count = 1                    # 行计数器始于 1
        self._flag = False           # 重置结束标志
        self.obj = MatrixObject()    # 一次只解析一个对象
        while not self._flag and self._pos < len(self._text):
            c = self._text[self._pos]
            self._pos += 1
            count = self._next(c, count)

        if not self.obj.has_name():  # 意外终止（无新对象）
            return None
        log.debug("parsed block @%s: %d rows x %d cols",
                  self.obj.name, self.obj.size, self.obj.col)
        return self.obj

    def _next(self, c, ct):
        """FSM 状态转移，与仓颉版 P.cj _next() 逐分支对齐（S02：无逐字符打印）"""
        count = ct
        # ------------ COMMENTS ------------
        if not self._cm and c == '#':                # 注释开始时
            self._cm = True
        elif self._cm and c == '\n':                 # 注释结束时
            self._nl = True
            self._cm = False
            count += 1
        elif self._cm:                               # 注释进行时
            pass
        # ------------ new line ------------
        elif self._nl and c in ' \t\r':              # 新行空字符，维持现状
            pass
        elif self._nl and c == '\n':                 # 新行空行
            count += 1
        elif self._nl and c == '@' and not self.obj.has_name():
            self._nl = False
            self._nm = True
        elif self._nl and c == '@' and self.obj.has_name():
            self._flag = True                        # 块结束
            self._nl = False
            self._nm = True
        elif self._nl:                               # 新行内容
            self._nl = False
            self.obj.add_rune(c)
        # ------------- not nl -------------
        elif self._nm and c in ' \t\n':              # 添加名字
            self.obj.submit_name()
            self._nl = True
            self._nm = False
        elif not self._nm and c in ' \t\n':          # 添加命名项（S04：'\n' 统一处理）
            self.obj.submit_item()
            if c == '\n':
                count += 1
        elif c == ';':                               # 普通行结束
            self.obj.submit_item()
            self.obj.submit_line(count)
            self._nl = True
        else:                                        # 非空格、非制表符、非分号
            self.obj.add_rune(c)
        return count


# ---------------- 矩阵式储存器 ----------------

class MatrixSaver:
    """接收 MatrixObject 容器与输出流，序列化回 .mt 格式"""

    def __init__(self, out):
        self._out = out

    def save(self, objects):
        for item in objects:
            item.save(self._out)
            self._out.write("\n")    # 对象之间空一行


# ---------------- 占位符语义（与仓颉版 MatrixVars_Hardware.getMt/parseMt 对齐） ----------------
#
# ⚠️ 与 actpred/mt_parser.py 的语义差异（不得误统一）：
#    actpred 数据管线首行 '-' 用 @Servo 初始角度填充；
#    pybot 运行时首行 '-' 数值列解析为 0（Servo 有 max/min 钳位保护）。

def get_mt(obj, row, col):
    """获得项目值：原始值是占位符且非首行（row>1），递归取上一行同列值"""
    raw = obj[row, col]
    if raw == "-" and row > 1:
        return get_mt(obj, row - 1, col)
    return raw


def parse_mt(obj, row, col, typ=float):
    """解析项目值：占位符递归向上；首行（row==1）占位符解析为 typ("0")"""
    raw = obj[row, col]
    if raw == "-" and row > 1:
        return parse_mt(obj, row - 1, col, typ)
    if raw == "-":
        return typ("0")
    return typ(raw)


def to_int(s):
    """整数解析，支持 0x 十六进制前缀（@PCA9685 address、@MPU6050_BIND addr 列）"""
    s = s.strip()
    if s.lower().startswith("0x") or s.lower().startswith("-0x"):
        return int(s, 16)
    return int(s)
