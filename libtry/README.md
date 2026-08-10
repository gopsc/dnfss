# libtry — 嵌入式脚本解释器

基于 C 语言手写的教学级脚本解释器（~741 行），魔改自 GitHub 上的 tryC 项目，用于解析 `.try` 脚本，设计目标为嵌入式操作界面 shell。

- **零依赖**：仅标准 C 库 + `console.h` 输出接口。
- **递归下降**：手写词法/语法/执行引擎，边解析边执行。
- **宽字符**：`wchar_t` 支持中文/多语言脚本。
- **嵌入式 API**：`init_tryc` / `enqueue_tryc` / `eval_tryc` / `free_tryc` 四个核心入口（单文件 [try.c](try.c) + `console.h` 输出接口）。

详细技术手册（含局限性与改进路线）见 [README.robot.md](README.robot.md)。
