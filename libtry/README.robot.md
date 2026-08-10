# libtry 模块技术手册

> 文档版本：2026-07-30
> 代码版本：try.c（~741 行）
> 语言：C（嵌入式解释器）
> 定位：教学级脚本解释器，用于操作界面 shell

---

## 一、模块概述

### 1.1 定位

**libtry** 是一个基于 C 语言手写的**教学级解释器**，魔改自 GitHub 上的 tryC 项目。用于解析 `.try` 脚本文件，设计目标是作为**操作界面的 shell**。

### 1.2 特点

| 特性 | 说明 |
|------|------|
| 手写解释器 | 仅 741 行实现完整的词法/语法/执行引擎 |
| 零依赖 | 仅依赖标准 C 库 + `console.h` 输出接口 |
| 宽字符支持 | 使用 `wchar_t`，支持中文/多语言脚本 |
| 递归下降 | 递归下降语法分析器，边解析边执行 |
| 嵌入式设计 | 提供 `init_tryc`/`enqueue_tryc`/`eval_tryc`/`free_tryc` 四个核心 API |

### 1.3 与微服务体系的关系

| 属性 | 说明 |
|------|------|
| 模块关系 | **独立底层库**，不直接与任何微服务交互 |
| 可能用途 | 作为前端 UI 的脚本解释器，或嵌入式脚本引擎 |
| 启动方式 | 被其他模块（可能是 gateway 或 static）动态加载调用 |

---

## 二、技术架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        上层应用                              │
│              (gateway / static / 其他模块)                   │
└─────────────────────────┬───────────────────────────────────┘
                          │ 调用 API
┌─────────────────────────▼───────────────────────────────────┐
│                     libtry 解释器                            │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                    词法分析器 (next)                     │ │
│  │  逐字符扫描 → 识别 token → 更新符号表                   │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │ token 流                      │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │              递归下降语法分析器                          │ │
│  │  statement() / expression() / factor() / term()          │ │
│  │  边解析边执行（解释器模式）                              │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │                                │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │                      符号表                               │ │
│  │  symtab[128] — 符号栈（变量/函数/数组）                  │ │
│  │  levelNum — 嵌套层级，支持作用域                        │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                     执行结果输出                             │
│         printtovga() / 控制台输出                            │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 执行流程

```
init_tryc() 初始化
    │
    ├── memset(symtab, 0, ...) 清零符号表
    ├── 预注册关键字 (array, func, else, if, return, while, print, puts, read)
    └── malloc(128) 分配源代码缓冲区
    │
enqueue_tryc(wc) 追加字符（交互式输入）
    │
    └── src[top_src] = wc; src[++top_src] = L'\0';
    │
eval_tryc() 执行
    │
    ├── next() 获取第一个 token
    ├── while (token != L'\0') { statement(); } 逐条执行
    └── 重置缓冲区
    │
free_tryc() 释放资源
    │
    └── free(p_src);
```

### 2.3 词法分析流程

```
next() 函数：
    │
    ├── 跳过空白符 (L' ', L'\t')
    ├── 跳过注释 (# 开头到换行)
    ├── 识别标识符 (字母/数字/下划线/中文)
    │   ├── 在符号表中搜索
    │   │   ├── 找到：根据 type 返回对应 token (Sym, FuncSym, ArraySym, Str)
    │   │   └── 未找到：创建新符号，类型为 Void
    ├── 识别数字 (整数 + 小数)
    ├── 识别字符字面量 ('a')
    ├── 识别字符串字面量 ("hello")
    ├── 识别运算符 (=, ==, !=, <, >, <=, >=, ||, &&)
    └── 识别标点符号 (;, ,, +, -, *, /, (, ), {, }, [, ])
```

---

## 三、代码结构

### 3.1 文件结构

```
libtry/
├── try.c              # 核心解释器实现（~741 行）
├── t.try              # 示例脚本（斐波那契数列）
├── test/              # 测试目录
│   ├── t_assign_basic.try    # 赋值测试
│   ├── t_function_basic.try  # 函数测试
│   └── t_output_basic.try    # 输出测试
├── .gitignore
└── README.robot.md    # 本文档
```

### 3.2 try.c 函数索引

| 函数 | 行号 | 功能 |
|------|------|------|
| `init_tryc()` | L650 | 初始化解释器，预注册关键字 |
| `free_tryc()` | L672 | 释放源代码缓冲区 |
| `enqueue_tryc(wc)` | L677 | 追加一个字符到缓冲区 |
| `eval_tryc()` | L687 | 解析并执行当前缓冲区脚本 |
| `next()` | L85 | 词法分析，获取下一个 token |
| `match(tk)` | L289 | 匹配并消费期望 token |
| `statement()` | L466 | 解析并执行语句 |
| `expression()` | L362 | 解析加减法表达式 |
| `term()` | L311 | 解析乘除法表达式 |
| `factor()` | L326 | 解析因子（变量/常量/函数调用/数组） |
| `function()` | L616 | 执行函数调用 |
| `boolexp()` | L378 | 解析布尔表达式 |
| `boolAND()` | L423 | 解析 AND 表达式（短路） |
| `boolOR()` | L438 | 解析 OR 表达式（短路） |
| `clear(level)` | L269 | 清理指定层级的符号 |
| `clearThis()` | L280 | 清理单个符号的动态内存 |
| `skipStatments()` | L453 | 跳过 if/else 未执行分支 |
| `skipBoolExpr()` | L413 | 跳过短路布尔表达式 |
| `__exit()` | L702 | 错误处理（死循环挂起） |
| `main()` | L710 | 文件执行模式（仅 `_DEBUG` 编译） |

### 3.3 核心数据结构

#### 符号表（symStruct）

```c
typedef struct symStruct {
    int type;                   // 类型：Num, Char, Str, Array, Func
    wchar_t name[16];          // 符号名（MAXNAMESIZE）
    double value;              // 值或数组/字符串长度
    union {
        wchar_t *funcp;       // 函数指针或字符串内容
        struct symStruct* list; // 数组元素指针
    } pointer;
    int levelNum;              // 嵌套层级（作用域）
} symbol;
```

#### Token 类型枚举

```c
enum {
    Num = 128,    // 数字
    Char,         // 字符字面量
    Str,          // 字符串
    Array,        // array 关键字
    Func,         // func 关键字
    Else,         // else 关键字
    If,           // if 关键字
    Return,       // return 关键字
    While,        // while 关键字
    Print,        // print 关键字
    Puts,         // puts 关键字
    Read,         // read 关键字
    Assign,       // = 赋值
    OR,           // ||
    AND,          // &&
    Equal,        // ==
    Sym,          // 变量符号
    FuncSym,      // 函数符号
    ArraySym,     // 数组符号
    Void,         // 未初始化
    Nequal,       // !=
    LessEqual,    // <=
    GreatEqual    // >=
};
```

#### 全局变量

| 变量 | 类型 | 说明 |
|------|------|------|
| `symtab[128]` | `symbol[]` | 符号表栈（SYMTABSIZE） |
| `symPointer` | `int` | 符号表栈指针 |
| `currentlevel` | `int` | 当前嵌套层级 |
| `src` | `wchar_t*` | 当前扫描位置 |
| `old_src` | `wchar_t*` | 行首位置（调试用） |
| `p_src` | `wchar_t*` | 源代码缓冲区起始 |
| `top_src` | `int` | 缓冲区写入位置 |
| `token` | `int` | 当前 token 类型 |
| `token_val` | `union tokenValue` | 当前 token 值 |
| `return_val` | `double` | 函数返回值 |
| `compileState` | `int` | 编译状态（debug/run） |

---

## 四、API 接口

### 4.1 初始化与销毁

#### init_tryc()

```c
int init_tryc(void);
```

**功能**：初始化解释器
- 清零符号表
- 预注册 8 个关键字（array, func, else, if, return, while, print, puts, read）
- 分配 128 字节宽字符缓冲区

**返回值**：
- `0`：成功
- `-1`：内存分配失败

#### free_tryc()

```c
void free_tryc(void);
```

**功能**：释放源代码缓冲区

### 4.2 输入与执行

#### enqueue_tryc(wc)

```c
int enqueue_tryc(wchar_t wc);
```

**功能**：追加一个字符到源代码缓冲区（用于交互式输入）

**参数**：
- `wc`：要追加的宽字符

**返回值**：
- `0`：成功
- `-1`：缓冲区已满

#### eval_tryc()

```c
void eval_tryc(void);
```

**功能**：解析并执行当前缓冲区中的脚本

**说明**：执行完成后会重置缓冲区。

### 4.3 典型使用示例

#### 交互式模式

```c
#include "try.c"

int main() {
    init_tryc();
    
    // 交互式输入
    wchar_t input;
    while (read_input(&input)) {
        enqueue_tryc(input);
        if (input == L'\n') {
            eval_tryc();  // 遇到换行执行
        }
    }
    
    free_tryc();
    return 0;
}
```

#### 文件执行模式

```c
#include "try.c"

int main(int argc, char **argv) {
    init_tryc();
    
    // 读取文件
    FILE *file = fopen(argv[1], "r");
    wchar_t wc;
    int i = 0;
    while ((wc = fgetwc(file)) != WEOF) {
        src[i++] = wc;
    }
    src[i] = L'\0';
    fclose(file);
    
    // 执行
    eval_tryc();
    
    clear(0);      // 清理全局符号
    free_tryc();
    return 0;
}
```

---

## 五、语言特性

### 5.1 数据类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `Num` | 数字（整数或浮点） | `a = 5;`, `b = 3.14;` |
| `Char` | 单个字符 | `c = 'A';` |
| `Str` | 字符串（宽字符） | `s = "hello";` |
| `Array` | 数组（动态分配） | `array arr(15);` |
| `Func` | 函数 | `func name{...};` |

### 5.2 变量与作用域

#### 变量声明

变量通过首次赋值自动声明：

```
x = 5;          // 声明并初始化数字变量
name = "hello"; // 声明并初始化字符串变量
flag = 'A';     // 声明并初始化字符变量
```

#### 作用域

- 全局变量：在顶层声明
- 局部变量：在函数内声明，函数结束后自动销毁
- 符号表使用栈结构，`levelNum` 标识嵌套层级

### 5.3 运算符

#### 算术运算符

| 运算符 | 说明 | 示例 |
|--------|------|------|
| `+` | 加法 | `a + b` |
| `-` | 减法 | `a - b` |
| `*` | 乘法 | `a * b` |
| `/` | 除法 | `a / b` |

#### 比较运算符

| 运算符 | 说明 | 示例 |
|--------|------|------|
| `>` | 大于 | `a > b` |
| `<` | 小于 | `a < b` |
| `>=` | 大于等于 | `a >= b` |
| `<=` | 小于等于 | `a <= b` |
| `==` | 等于 | `a == b` |
| `!=` | 不等于 | `a != b` |

#### 逻辑运算符

| 运算符 | 说明 | 示例 |
|--------|------|------|
| `\|\|` | 逻辑或（短路） | `a \|\| b` |
| `&&` | 逻辑与（短路） | `a && b` |
| `!` | 逻辑非 | `!a` |

#### 赋值运算符

| 运算符 | 说明 | 示例 |
|--------|------|------|
| `=` | 赋值 | `a = 5;` |

### 5.4 控制流

#### if/else 语句

```
if (condition) {
    // true 分支
}
else {
    // false 分支
}
```

#### while 循环

```
while (condition) {
    // 循环体
}
```

#### return 语句

```
func name{
    return(value);
};
```

### 5.5 函数

#### 函数定义

```
func function_name{
    // 函数体
    return(return_value);
};
```

**特性**：
- 支持递归调用
- 支持参数传递（通过符号表）
- `return` 语句会终止函数执行并返回值

#### 函数调用

```
result = function_name(arg1, arg2);
```

### 5.6 数组

#### 数组声明

```
array arr(size);
```

- `size` 必须是正整数
- 数组元素初始化为 0
- 数组元素类型为 `Num`（double）

#### 数组访问与赋值

```
arr[0] = 10;
value = arr[i];
```

### 5.7 输入输出

#### print()

```
print(expression);
```

**功能**：输出数字

**示例**：
```
print(42);           // 输出: 42.000000
print(3.14);         // 输出: 3.140000
print(arr[0]);       // 输出数组元素
```

#### puts()

```
puts("string");
```

**功能**：输出字符串

**示例**：
```
puts("Hello, World!");
```

#### read()

```
read(variable);
```

**功能**：读取输入（当前实现返回 -1）

### 5.8 注释

```
# This is a comment
```

注释从 `#` 开始到行尾结束。

---

## 六、示例脚本

### 6.1 斐波那契数列

```
# Fibonacci sequence
func fun{ 
    if(x <= 2){  
        return(1);  
    }
    y = 0;
    x = x - 1;
    y = fun(x);
    x = x - 1;
    return(y + fun(x));
};

# 计算 1-15 的斐波那契数列并存入数组
array arr(15);	
x = 0;
while(x<15){
    arr[x] = fun(x) + 1000;
    x = x + 1;
}

puts("Fibonacci sequence:");
i = 0;
while(i < 15){			
    print(arr[i]);
    i=i+1;
}
```

### 6.2 基础赋值

```
a = 5;
b = "ok";

print(5);
puts(b);
```

### 6.3 基础函数

```
func f_test{ 
    return(0);
};

r = f_test();
print(r);
```

### 6.4 基础输出

```
print(1);
puts("1");
```

---

## 七、已知限制

### 🔴 严重问题

| ID | 位置 | 描述 | 影响 |
|----|------|------|------|
| L1 | [L20](try.c#L20) | **缓冲区大小固定**：`POOLSIZE = 128` 字节，无法修改 | 长脚本将溢出 |
| L2 | [L702](try.c#L702) | **`__exit()` 死循环**：`while(1);` 错误时直接挂死程序 | 无法恢复 |
| L3 | [L186](try.c#L186) | **字符串内存泄漏**：`malloc` 分配的字符串在重新赋值时未释放 | 长期运行泄漏 |
| L4 | [L528](try.c#L528) | **数组越界**：无越界检查 | 未定义行为 |

### 🟡 中等问题

| ID | 位置 | 描述 |
|----|------|------|
| L5 | [L598](try.c#L598) | **`puts` 实现存疑**：原 `wprintf` 被注释，改用 `printtovga` |
| L6 | [L518](try.c#L518) | **`clearThis()` 被注释**：作者注释说"不知为何需要注释了才能跑" |
| L7 | [L706](try.c#L706) | **文件模式仅限 `_DEBUG`**：正常构建时 `main()` 不编译 |
| L8 | [L103](try.c#L103) | **栈缓冲区溢出风险**：`nameBuffer[16]` 但标识符长度限制由 `MAXNAMESIZE=16` 控制 |
| L9 | [L21](try.c#L21) | **符号表大小固定**：`SYMTABSIZE = 128` 个符号 |

### 🟢 轻微问题

| ID | 描述 |
|----|------|
| L10 | 错误处理过于简单，`match()` 失败直接 `__exit(-1)` |
| L11 | 大量被注释的调试代码（`//wprintf(...)`） |
| L12 | 全局变量过多（`symtab`, `token`, `token_val`, `src`, `old_src`, `currentlevel` 等） |
| L13 | 无并发支持，非线程安全 |
| L14 | 无单元测试 |

### 修复建议

#### 优先级 1（安全相关）

1. **修复数组越界**：在 [L528](try.c#L528) 添加检查
   ```c
   if (index < 0 || index >= s->value) {
       printtovga(L"Array index out of bounds\n");
       __exit(-1);
   }
   ```

2. **修复 `__exit()`**：改为返回错误码
   ```c
   static int error_code = 0;
   static void __exit(int code) {
       error_code = code;
       // 不挂起，让调用者处理错误
   }
   ```

3. **修复字符串内存泄漏**：在赋值前释放旧字符串
   ```c
   else if (token == Str) {
       if (s->pointer.funcp != NULL) {
           free(s->pointer.funcp);  // 释放旧字符串
       }
       s->pointer.funcp = (wchar_t*)token_val.ptr;
       s->type = Str;
       match(Str);
   }
   ```

#### 优先级 2（功能增强）

4. **动态缓冲区**：将 `POOLSIZE` 改为可配置或动态扩容
5. **错误回调**：添加错误回调函数，让调用者处理错误
6. **符号表扩容**：允许 `SYMTABSIZE` 动态增长

---

## 八、开发指南

### 8.1 添加新关键字

在 [init_tryc()](try.c#L650) 中添加：

```c
// 1. 在 enum 中添加新 token 类型
enum {
    Num = 128, Char, Str, Array, Func,
    Else, If, Return, While, Print, Puts, Read,
    Assign, OR, AND, Equal, Sym, FuncSym, ArraySym, Void,
    Nequal, LessEqual, GreatEqual,
    NewKeyword  // 新增
};

// 2. 在 init_tryc() 中注册
src = L"array func else if return while print puts read new_keyword";
for (i = Array; i <= NewKeyword; ++i) {
    next();
    symtab[symPointer - 1].type = i;
}

// 3. 在 statement() 中添加处理逻辑
else if (token == NewKeyword) {
    match(NewKeyword);
    // 处理逻辑
}
```

### 8.2 添加新运算符

在 [next()](try.c#L85) 中添加识别逻辑：

```c
// 例如：+= 运算符
else if (token == L'+') {
    if (*src == L'=') {
        src++;
        token = AddAssign;  // 新增 token 类型
    }
}
```

在 [statement()](try.c#L466) 中添加执行逻辑。

### 8.3 自定义输出

修改 `printtovga()` 的实现，或添加可配置的输出回调：

```c
// 函数指针类型
typedef void (*output_func)(const wchar_t*);

// 全局输出函数
static output_func current_output = default_output;

// 设置输出函数
void set_output_func(output_func func) {
    current_output = func;
}
```

### 8.4 构建说明

#### 编译器要求

- C 编译器（GCC/Clang）
- 支持 C99 标准
- 支持宽字符（`wchar.h`）

#### 编译选项

```bash
# 基本编译
gcc -c try.c -o try.o -std=c99

# 调试模式（启用 main 函数）
gcc -c try.c -o try.o -std=c99 -D_DEBUG

# 优化编译
gcc -c try.c -o try.o -std=c99 -O2
```

#### 链接使用

```bash
# 创建测试程序
cat > test_main.c << 'EOF'
#include <stdio.h>
#include "try.h"

int main() {
    init_tryc();
    
    // 注入脚本
    wchar_t script[] = L"print(42);";
    for (int i = 0; script[i] != L'\0'; i++) {
        enqueue_tryc(script[i]);
    }
    eval_tryc();
    
    free_tryc();
    return 0;
}
EOF

# 编译链接
gcc test_main.c try.o -o test_try -std=c99
./test_try
```

---

## 九、测试

### 9.1 现有测试

| 测试文件 | 内容 |
|----------|------|
| `test/t_assign_basic.try` | 变量赋值与输出 |
| `test/t_function_basic.try` | 函数定义与调用 |
| `test/t_output_basic.try` | print/puts 输出 |
| `t.try` | 斐波那契数列（复杂示例） |

### 9.2 运行测试

```bash
# 编译（调试模式）
gcc try.c -o try_interp -std=c99 -D_DEBUG

# 执行测试脚本
./try_interp test/t_assign_basic.try
./try_interp test/t_function_basic.try
./try_interp test/t_output_basic.try
./try_interp t.try
```

### 9.3 添加新测试

在 `test/` 目录下创建 `.try` 文件：

```
# test/test_new_feature.try
# 描述新功能

a = 10;
b = 20;
c = a + b;
print(c);
```

---

## 十、版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| 当前 | 2025-08-27 | 增加宽字符支持，修复 Str 匹配 BUG，使用 longjmp 替代 exit() |
| 初始 | — | 魔改自 tryC 项目，手写简易解释器 |

### 修改记录（代码注释）

| 日期 | 作者 | 变更 |
|------|------|------|
| 2025-08-27 | qing | 增加宽字符支持 |
| 2025-08-27 | qing | 修复 Str 漏匹配的 BUG |
| 2025-08-27 | qing | 注明内存泄漏风险 |
| 2025-08-27 | qing | 使用 `longjmp()` 替代 `exit()` |
| 2026-07-07 | qing | 增加 `clearThis()` 函数，用于释放变量 |

---

## 附录

### A. 完整关键字列表

```
array, func, else, if, return, while, print, puts, read
```

### B. Token 类型完整列表

```
Num(128), Char(129), Str(130), Array(131), Func(132),
Else(133), If(134), Return(135), While(136), Print(137), Puts(138), Read(139),
Assign(140), OR(141), AND(142), Equal(143), Sym(144), FuncSym(145), ArraySym(146), Void(147),
Nequal(148), LessEqual(149), GreatEqual(150)
```

### C. 限制常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `POOLSIZE` | 128 | 源代码缓冲区大小（字节） |
| `SYMTABSIZE` | 128 | 符号表大小 |
| `MAXNAMESIZE` | 16 | 标识符最大长度 |
| `RETURNFLAG` | DBL_MAX | return 语句标记 |

### D. 参考资源

- [Crafting Interpreters](https://craftinginterpreters.com/) — 解释器编写经典教程
- [Writing an Interpreter in Go](https://interpreterbook.com/) — Go 实现的解释器教程
- [tryC (GitHub)](https://github.com/) — 原始项目（已魔改）

---

*本文档基于 try.c 源码分析生成，供开发团队参考。使用前请评估已知限制。*
