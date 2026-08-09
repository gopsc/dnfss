# captcha 模块 C++20 重写 · 开发方案

> 文档类型：模块重写开发计划
> 模块：captcha（验证码服务）
> 日期：2026-08-09
> 参照：dnfs2 / db2 / fsys 的 Python → C++20 重写路径

---

## 1. 概述

### 1.1 背景与动机

captcha 模块是 micro_servers 中唯一仍未 C++ 化的纯 Python 内部基础服务（smtp 同为 Python 但依赖外部 SMTP 会话，重写收益有限）。当前 Python 版核心只有 299 行，依赖仅 Pillow + pymsg，属于**小而精的理想重写目标**。

| 维度 | Python 版现状 | 重写收益 |
|------|-------------|----------|
| 依赖 | Python venv + Pillow + pymsg 本地包 | 消除 Python 运行时依赖，编译为单二进制 |
| 启动 | `_run.sh` 激活 venv 后 `exec python3` | 与 db2/dnfs2/fsys 一致的 `./captcha2.bin` 直接启动 |
| 性能 | PIL 图片生成是 CPU 密集型，单线程 GIL 受限 | 原生 C++ 无 GIL，`workers>0` 时真实并发 |
| 审计遗留 | P2 `random.choices` 非安全随机、NEW-2 无速率限制 | 一并用 `std::mt19937` + `random_device` 修复 |
| 一致性 | 项目已有 3 个成功的 C++ 重写案例（db2/dnfs2/fsys） | 与 gateway/db2/dnfs2/fsys 统一技术栈 |

### 1.2 设计目标

1. **协议完全兼容**：pymsg 三条路由（`image` / `verify` / `health`）请求/响应格式与 Python 版字节级一致，gateway 零改动
2. **图片效果一致**：160×50 PNG，相同干扰策略（旋转字符 + 干扰线 + 噪点），视觉效果等同，机器识别难度不降低
3. **安全对齐**：修复审计报告中的 `random` → 安全随机、添加生成速率限制
4. **零新增重型依赖**：仅引入 FreeType（libfreetype-dev），已经是 Linux 标配；PNG 编码使用单头文件 stb_image_write.h（公共领域）
5. **基础设施复用**：libmsg（RpcServer/AsyncQueue）、boost::json、Conf.hpp 模式，所有已在项目内的组件

### 1.3 与 gateway 内置 CAPTCHA 的关系

gateway 内部保留了一套旧版本地 CAPTCHA（`generate_captcha_text()` + `verify_captcha()` + `_CAPTCHA_STORE`），与 captcha 服务通过 pymsg 调用的 `verify_captcha_remote()` **并存**。本次重写仅涉及独立的 captcha 服务进程，不影响 gateway 内部逻辑。

---

## 2. 现状分析

### 2.1 源码结构

```
captcha/
├── captcha_service.py   # 299 行，主程序
├── log_client.py        # 159 行，异步日志客户端（项目通用 SDK）
├── _set.sh              # venv 创建 + pip install
├── _run.sh              # venv 激活 + exec python3
└── requirements.txt     # ../pymsg + Pillow
```

### 2.2 功能清单

| 功能 | 代码位置 | 行数 | C++ 等效方案 |
|------|---------|------|-------------|
| 配置加载（confs → env） | L40–134 | 95 行 | 复用 gateway `Conf.hpp` 的 confs 拉取 + env 覆盖模式 |
| `generate_text()` | L145–146 | 2 行 | `std::mt19937` + 字符集数组（审计 NEW-1 修复：用 `random_device` 种子） |
| `generate_image()` | L149–182 | 34 行 | FreeType 渲染 + 像素旋转合成 + stb_image_write PNG 编码（约 180 行 C++） |
| `CaptchaServer.__init__` | L192–198 | 7 行 | `RpcServer` 构造 + `add_route` 注册三个路由 |
| `_handle_image` | L215–241 | 27 行 | 逻辑完全相同：生成 text → 惰性清理 → 容量检查 → 存 store → 调 generate_image → base64 编码返回 |
| `_handle_verify` | L243–269 | 27 行 | 逻辑完全相同：pop token → 不存在返回 invalid → XOR 恒定时间比较 |
| `_handle_health` | L271–272 | 2 行 | 返回 `{"status":"ok","service":"captcha"}` |
| `main` / argparse | L279–299 | 21 行 | boost::program_options（与 gateway 一致） |
| log_client | 整个文件 | 159 行 | 基于 libmsg `AsyncQueue`，参照 gateway `LogC.hpp`（约 60 行） |

### 2.3 `generate_image()` 详细分析

当前 Python 版图片生成流程（这是 C++ 重写的核心难点）：

```
1. Image.new('RGB', (160, 50), random_bg_color)
   → 创建 RGB 画布，随机背景色

2. for each char in text (4 chars):
   a. random_color → 随机前景色
   b. random_y (2~12) → 垂直偏移
   c. random_x (15 + i*32 ± 3) → 水平分布在 160px 宽画布上
   d. ImageFont.truetype(font_path, random_size 20~38) → 可变字号
   e. 创建 64×64 透明子画布 → draw_char.text((20,20), char)
   f. char.rotate(random_angle ±30°) → 旋转字符
   g. img.paste(rotated_char, (x-32, y-32), mask=rotated_char)
      → Alpha 合成到主画布

3. 4 条随机干扰线（Bresenham）

4. 50 个随机噪点（单像素）

5. img.save(buf, format='PNG') → PNG 编码
```

每个步骤的 C++ 等价实现：

| 步骤 | Python (Pillow) | C++ (FreeType + 手写) |
|------|----------------|----------------------|
| 1. 创建画布 | `Image.new('RGB', 160, 50)` | `std::vector<uint8_t> pixels(160*50*3)` |
| 2a. 随机颜色 | `random.randint(0,256)` | `std::uniform_int_distribution<int>(0, 255)` |
| 2d. 字体加载 | `ImageFont.truetype(path, size)` | `FT_New_Face` + `FT_Set_Char_Size` |
| 2e. 字符渲染 | `draw_char.text((20,20), ch)` | `FT_Load_Char(face, ch, FT_LOAD_RENDER)` → 灰度位图 |
| 2f. 旋转 | `char.rotate(angle, expand=True)` | 手写最近邻插值旋转（~40 行） |
| 2g. Alpha 合成 | `img.paste(char, pos, mask=char)` | 像素级 alpha blend（灰度值 → 透明度） |
| 3. 干扰线 | `draw.line([...])` | Bresenham 画线算法 |
| 4. 噪点 | `draw.point((x, y))` | 直接写像素 |
| 5. PNG 编码 | `img.save(buf, format='PNG')` | `stbi_write_png_to_func` |

### 2.4 依赖替换矩阵

| Python 依赖 | C++ 替代 | 引入方式 |
|------------|---------|----------|
| `Pillow` (PIL) | FreeType 2 + stb_image_write.h | `apt install libfreetype-dev` + 单头文件放入 `inc/` |
| `pymsg` | libmsg (`RpcServer` / `AsyncQueue`) | 系统已安装 (`libus_msg.so`) |
| `log_client.py` | 独立 `LogC.hpp`（基于 `AsyncQueue`） | 参照 gateway `LogC.hpp` 简化版 |
| `configparser` | boost::property_tree（`Conf.hpp` 模式） | 系统已安装 (`libboost-all-dev`) |
| `argparse` | boost::program_options | 系统已安装 |
| `secrets` / `random` | `<random>` (`mt19937` + `random_device`) | C++11 标准库 |
| `base64` | libcrypt `Base64` 或手写（~20 行） | 系统已安装 (`libus_crypt.so`) 或内联 |
| `threading.Lock` | `std::mutex` + `std::lock_guard` | C++11 标准库 |
| `json` | `boost::json` | 系统已安装 |

---

## 3. 整体设计

### 3.1 模块结构

```
captcha2/
├── src/
│   └── main.cpp              # ~420 行，主程序
│       ├── 配置加载           # confs captcha.ini → env override（~80 行）
│       ├── CaptchaStore       # 线程安全 Token 存储（~40 行）
│       ├── generate_text()    # 安全随机文本生成（~15 行）
│       ├── generate_image()   # FreeType + 像素操作 + PNG 编码（~180 行）
│       ├── base64_encode()    # 手写或复用 libcrypt（~20 行）
│       └── main()             # RpcServer 路由注册 + 启动（~85 行）
├── inc/
│   ├── stb_image_write.h      # 单头文件 PNG 编码器（公共领域，~1800 行）
│   └── LogC.hpp               # 简化版异步日志客户端（~60 行）
├── Makefile                    # 构建（~30 行）
├── _set.sh                     # make + install（~10 行）
├── _run.sh                     # 启动脚本（~5 行）
└── README.md
```

### 3.2 与现有 C++ 基础库的关系

```
captcha2.bin
    │
    ├── libmsg (RpcServer)       ← pymsg 协议 C++ 实现，路由分发 + 响应包装
    │     └── libus_msg.so       （系统已安装）
    │
    ├── libmsg (AsyncQueue)      ← 异步批量日志推送
    │     └── libus_msg.so
    │
    ├── boost::json              ← JSON 解析/序列化
    │     └── libboost_json      （系统已安装）
    │
    ├── boost::program_options   ← 命令行参数解析
    │     └── libboost_program_options（系统已安装）
    │
    ├── FreeType 2               ← 字体渲染
    │     └── libfreetype.so     （需新增，apt install libfreetype-dev）
    │
    └── stb_image_write.h        ← PNG 编码（header-only，直接放入 inc/）
```

### 3.3 配置体系

与 gateway/db2/dnfs2/fsys 一致的**两层加载**模式：

1. **confs 统一配置** → 启动时 pymsg `get` 路由拉取 `captcha.ini`（`retry_on_refused=true` 阻塞等待 confs 就绪）
2. **环境变量覆盖**（最高优先级，Docker 兼容）

新增可选配置项（审计 NEW-2 修复）：

```ini
[captcha]
len = 4                  # 验证码字符数（不变）
ttl = 300                # Token 有效期，秒（不变）
font_path = /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf  # 字体路径（不变）
max_store = 10000        # 最大 Token 存储数（Python 版已有）
rate_limit_per_min = 60  # [新增] 每分钟最多生成次数，0=不限制，默认 60
```

配置白名单合并（复用 Python 版的 `_merge_ini` 策略，仅覆盖已知 section/key）：

```cpp
struct CaptchaConfig {
    // [server]
    std::string host = "127.0.0.1";
    int port = 8009;
    int workers = 4;            // RpcServer 工作线程数

    // [captcha]
    int len = 4;
    int ttl = 300;
    std::string font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf";
    int max_store = 10000;
    int rate_limit_per_min = 60;  // 0 = 不限制
};
```

---

## 4. 详细设计

### 4.1 CaptchaStore — 线程安全 Token 存储

```cpp
class CaptchaStore {
public:
    struct Entry {
        std::string answer;       // 小写答案
        std::chrono::steady_clock::time_point created;
    };

    // 生成新 Token，返回 token 字符串；容量满时抛出 std::runtime_error
    std::string generate(const std::string& answer, int ttl_seconds, int max_size);

    // 校验并消费 Token（一次性）；返回 true 表示答案正确
    // 无效/过期 Token 返回 false
    bool verify(const std::string& token, const std::string& answer, int ttl_seconds);

    // 当前存储数量
    size_t size() const;

private:
    void expire_locked(int ttl_seconds);  // 惰性清理过期项

    mutable std::mutex mtx_;
    std::unordered_map<std::string, Entry> store_;
};
```

**与 Python 版的关键差异：**
- `generate()` 和 `verify()` 都在锁内先清理过期项
- Token 生成使用 256-bit 安全随机（`std::random_device` + `std::mt19937` + hex 编码），替代 Python 版 `secrets.token_hex(16)` 的 128-bit → 升级为 256-bit
- 容量检查同 Python 版：清理过期后仍超限则拒绝生成

### 4.2 generate_text() — 安全随机验证码文本

```cpp
std::string generate_text(int len = 4) {
    // 剔除易混淆字符：l I 1 O 0 o（与 Python 版完全一致）
    static const char kChars[] =
        "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
    static constexpr size_t kNumChars = sizeof(kChars) - 1;

    // thread_local 保证多线程安全（RpcServer workers>0 时并发回调）
    thread_local std::mt19937 rng(std::random_device{}());
    std::uniform_int_distribution<size_t> dist(0, kNumChars - 1);

    std::string text;
    text.reserve(len);
    for (int i = 0; i < len; ++i)
        text.push_back(kChars[dist(rng)]);
    return text;
}
```

**审计修复**：Python 版使用 `random.choices`（确定性伪随机），C++ 版用 `std::random_device` 作为种子源，消除可预测性风险。

### 4.3 generate_image() — 核心图片生成（FreeType + 像素操作）

这是重写的**技术核心**，约 180 行 C++。以下为伪代码级设计：

```cpp
// 返回值：PNG 字节流
std::vector<uint8_t> generate_image(const std::string& text,
                                     const std::string& font_path) {
    constexpr int kWidth = 160, kHeight = 50, kChannels = 3;
    std::vector<uint8_t> pixels(kWidth * kHeight * kChannels);

    // 1. 随机背景色
    fill_random_color(pixels, kWidth, kHeight);

    // 2. 初始化 FreeType
    FT_Library ft;
    FT_Init_FreeType(&ft);
    FT_Face face;
    // 字体缺失时回退逻辑：先尝试指定路径 → 再尝试默认 DejaVu → 失败则报错退出
    if (FT_New_Face(ft, font_path.c_str(), 0, &face) != 0)
        FT_New_Face(ft, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0, &face);

    // 3. 逐字符渲染
    thread_local std::mt19937 rng(std::random_device{}());
    std::uniform_int_distribution<int> angle_dist(-30, 30);
    std::uniform_int_distribution<int> size_dist(20, 38);
    std::uniform_int_distribution<int> y_dist(2, 12);
    std::uniform_int_distribution<int> x_jitter(-3, 3);

    for (size_t i = 0; i < text.size(); ++i) {
        // 随机颜色、字号、旋转角度、位置
        auto color = random_color(rng);
        int font_size = size_dist(rng);
        FT_Set_Char_Size(face, 0, font_size * 64, 96, 96);

        int angle = angle_dist(rng);
        int y = y_dist(rng);
        int x = 15 + static_cast<int>(i) * 32 + x_jitter(rng);

        // FT_Load_Char 渲染为 8-bit 灰度位图
        FT_Load_Char(face, text[i], FT_LOAD_RENDER);

        // 旋转灰度位图（最近邻插值，expand 模式）
        auto rotated = rotate_bitmap(face->glyph->bitmap, angle);

        // Alpha 合成到主画布
        composite_char(pixels, kWidth, kHeight, rotated, x, y, color);
    }

    FT_Done_Face(face);
    FT_Done_FreeType(ft);

    // 4. 4 条随机干扰线
    for (int i = 0; i < 4; ++i) {
        draw_line(pixels, kWidth, kHeight,
                  random_point(rng), random_point(rng),
                  random_color(rng));
    }

    // 5. 50 个随机噪点
    for (int i = 0; i < 50; ++i) {
        set_pixel(pixels, kWidth, kHeight,
                  random_point(rng), random_color(rng));
    }

    // 6. PNG 编码
    std::vector<uint8_t> png_buf;
    stbi_write_png_to_func(
        [](void* ctx, void* data, int size) {
            auto* buf = static_cast<std::vector<uint8_t>*>(ctx);
            buf->insert(buf->end(), (uint8_t*)data, (uint8_t*)data + size);
        },
        &png_buf, kWidth, kHeight, kChannels,
        pixels.data(), kWidth * kChannels);

    return png_buf;
}
```

**关键辅助函数**：

| 函数 | 说明 | 预估行数 |
|------|------|----------|
| `fill_random_color(pixels, w, h)` | 填充随机背景色 | 10 |
| `random_color(rng)` | 返回 `(R, G, B)` 结构体 | 5 |
| `rotate_bitmap(bitmap, angle)` | 最近邻插值旋转灰度位图，expand 模式 | 40 |
| `composite_char(pixels, w, h, rotated, x, y, color)` | 将旋转后字符位图以指定颜色 Alpha 合成到主画布；灰度值作为透明度 | 25 |
| `draw_line(pixels, w, h, p1, p2, color)` | Bresenham 画线 | 20 |
| `set_pixel(pixels, w, h, point, color)` | 设置单像素（带边界检查） | 8 |
| `random_point(rng)` | 返回画布内随机点 `(x, y)` | 5 |
| `stbi_write_png_to_func` 的回调封装 | 将 stb 输出收集到 `vector<uint8_t>` | 10 |

**FreeType 字体回退策略**（与 Pillow 行为对齐）：

```
1. 尝试 FT_New_Face(font_path) → 成功则使用
2. 失败 → 尝试 DejaVuSans-Bold.ttf 默认路径
3. 失败 → 打印错误到 stderr，退出进程（验证码无字体无法工作）
   （Python 版 Pillow 回退到 load_default() 位图字体无抗锯齿视觉效果差；
    C++ 版选择快速失败，在部署阶段确保字体安装）
```

### 4.4 RpcServer 路由注册

```cpp
int main(int argc, char* argv[]) {
    // 1. 命令行解析（boost::program_options）
    // 2. 配置加载：confs captcha.ini → 环境变量覆盖 → 完整性校验
    // 3. 初始化 CaptchaStore、字体路径
    // 4. 初始化日志客户端（AsyncQueue → db Logs 表）

    pmc::net::RpcServer rpc(config.host, config.port,
        {.workers = config.workers});

    CaptchaStore store;

    // 路由
    rpc.add_route("image", [&](const json::object&) -> json::value {
        // 速率限制检查（审计 NEW-2）
        std::string text = generate_text(config.len);
        std::string token = store.generate(text, config.ttl, config.max_store);
        auto png = generate_image(text, config.font_path);
        json::object data;
        data["token"] = token;
        data["image"] = base64_encode(png);
        return data;
    });

    rpc.add_route("verify", [&](const json::object& req) -> json::value {
        std::string token = json::value_to<std::string>(req.at("token"));
        std::string answer = json::value_to<std::string>(req.at("answer"));
        // 小写化
        std::transform(answer.begin(), answer.end(), answer.begin(), ::tolower);
        bool valid = store.verify(token, answer, config.ttl);
        json::object data;
        data["valid"] = valid;
        return data;
    });

    rpc.add_route("health", [](const json::object&) -> json::value {
        json::object data;
        data["status"] = "ok";
        data["service"] = "captcha";
        return data;
    });

    logs.info("captcha", "开始监听 tcp://" + config.host + ":" + std::to_string(config.port));
    rpc.start();  // 阻塞
}
```

### 4.5 LogC.hpp — 简化版异步日志客户端

参照 gateway `LogC.hpp`，基于 libmsg `AsyncQueue<LogEntry>`：

```cpp
// ~60 行，与 log_client.py 功能等价
// - 异步批量推送（攒满 50 条或 2 秒 flush）
// - fail-safe（db 不可用静默丢弃）
// - push()/info()/warning()/error() API 不变
// - 环境变量 DB_HOST/DB_PORT 覆盖 db 地址
```

### 4.6 速率限制（审计 NEW-2 修复）

在 `image` 路由中增加滑动窗口限流：

```cpp
class RateLimiter {
public:
    // 返回 true 表示允许，false 表示超限
    bool allow(int max_per_minute = 60) {
        std::lock_guard<std::mutex> lock(mtx_);
        auto now = std::chrono::steady_clock::now();

        // 清理超过 60 秒的旧记录
        while (!timestamps_.empty() &&
               std::chrono::duration_cast<std::chrono::seconds>(
                   now - timestamps_.front()).count() > 60) {
            timestamps_.pop_front();
        }

        if ((int)timestamps_.size() >= max_per_minute)
            return false;

        timestamps_.push_back(now);
        return true;
    }

private:
    std::deque<std::chrono::steady_clock::time_point> timestamps_;
    std::mutex mtx_;
};
```

通过在 `_handle_image` 入口处调用 `rate_limiter.allow()` 实现，超限时返回：

```json
{"success": false, "error": "rate limit exceeded, please try again later"}
```

---

## 5. 实现计划

### 5.1 分阶段交付

| 阶段 | 里程碑 | 关键产出 | 预估工时 |
|------|--------|----------|----------|
| **P0 — 基础设施** | 目录骨架 + Makefile + stb 引入 + LogC.hpp | `captcha2/` 结构搭建，能编译出空 `main()` 链接 libmsg + FreeType | 1h |
| **P1 — 图片生成核心** | `generate_image()` 全链路 | FreeType 渲染 + 旋转 + 合成 + 干扰线 + 噪点 + PNG 编码，视觉效果与 Python 版对比 | 5h |
| **P2 — CaptchaStore** | Token 存储 + 过期清理 + 容量保护 | 线程安全 `generate()` / `verify()`，单元测试 | 1.5h |
| **P3 — RPC 服务集成** | `RpcServer` 三路由 + 命令行参数 | 能与 gateway 联调，pymsg 协议互通 | 2h |
| **P4 — 配置加载** | confs 拉取 + env 覆盖 + 配置完整性校验 | 与现有 `captcha.ini` 完全兼容 | 1.5h |
| **P5 — 安全加固** | 速率限制 + `mt19937` 安全种子 + 审计遗留修复 | 审计报告 NEW-1/NEW-2 全部修复 | 1h |
| **P6 — 日志集成** | LogC.hpp 异步推送日志到 db | 结构化日志（生成/校验/错误），与 Python 版日志格式对齐 | 1h |
| **P7 — 测试** | 黑盒测试 + 图片对比 + 压力测试 | `tests/test_captcha2.py` | 2.5h |
| **P8 — 文档 + 部署** | README + _set.sh/_run.sh + init.txt 更新 | 完整文档 + 一键安装 + core 自启动 | 0.5h |

**总计预估：~16 小时**

### 5.2 P1 图片生成是高风险路径

P1 阶段需要逐像素对比 Python/C++ 版本的输出，验证视觉效果一致。策略：

1. **单元级对比**：对同一输入文本，分别运行 Python 和 C++ 版 `generate_image()`，保存 PNG 文件进行人工对比
2. **关键差异容忍**：由于 `random` vs `mt19937` 的伪随机序列不同、FreeType 与 Pillow 的字体 hinting 差异，逐像素精确匹配不可能。目标是**同等干扰级别、同等人类可读性、同等机器识别难度**
3. **灰度验证**：将两张图片都转灰度后，对比字符位置分布、旋转角度范围、噪点密度等统计特征

### 5.3 构建系统

```makefile
# captcha2/Makefile
CXX      = g++
CXXFLAGS = -std=c++20 -Wall -O2
INCS     = -I./inc -I/usr/include/freetype2
LIBS     = -lus_msg -lboost_json -lboost_program_options -lfreetype -lpthread

TARGET   = captcha2.bin
SRC      = src/main.cpp

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCS) $^ $(LIBS) -o $@

install: $(TARGET)
	cp $(TARGET) /usr/local/bin/

clean:
	rm -f $(TARGET)
```

> **说明**：stb_image_write.h 是单头文件（header-only），无需编译链接，直接 `#include` 并在一个翻译单元中 `#define STB_IMAGE_WRITE_IMPLEMENTATION`。FreeType 需链接 `-lfreetype`。

---

## 6. 测试计划

### 6.1 黑盒测试（`tests/test_captcha2.py`）

复用现有测试框架（`tests/blackbox/helpers.py` 中的 `PymsgClient`），覆盖：

| 测试用例 | 说明 |
|----------|------|
| `test_image_generation` | 调 `image` 路由，验证返回合法 token + base64 PNG（解码后为有效 PNG，尺寸 160×50） |
| `test_verify_correct` | `image` → `verify(correct_answer)` → `{"valid": true}` |
| `test_verify_wrong` | `image` → `verify(wrong_answer)` → `{"valid": false}` |
| `test_verify_replay` | `image` → `verify(correct)` → `verify(correct)` → 第二次应返回 `invalid token`（一次性消费） |
| `test_verify_expired` | 生成后等待 CAPTCHA_TTL+1 秒再校验 → `invalid token` |
| `test_verify_no_token` | `verify` 缺少 token 字段 → `{"valid": false, "error": "token and answer required"}` |
| `test_health` | 调 `health` 路由 → `{"status": "ok", "service": "captcha"}` |
| `test_max_store` | 连续生成超过 `max_store` 个 → 后续返回 `too many pending captchas` |
| `test_rate_limit` | 连续生成超过 `rate_limit_per_min` 次 → 返回 `rate limit exceeded`（与 Python 版的区别：Python 版暂无限流） |

### 6.2 图片质量验证

| 方法 | 说明 |
|------|------|
| 人工 A/B 对比 | 同一文本（固定随机种子），分别用 Python 和 C++ 生成，人工对比 |
| 统计特征 | 字符分布范围、旋转角度范围、干扰线数量、噪点密度 → 与 Python 版统计对齐 |
| OCR 挑战 | 用简单 OCR 工具（如 tesseract）分别尝试识别两版验证码，识别率应相近 |

### 6.3 gateway 联调

1. 启动 captcha2 服务替代 Python captcha
2. `GET /captcha/image` → 浏览器正常显示验证码图片
3. 登录/注册流程完整走通
4. `X-Captcha-Token` 响应头正常

---

## 7. 迁移计划

### 7.1 渐进切换

```
阶段 1: captcha2.bin 开发完成，通过全部测试
        ↓
阶段 2: 在测试环境与 Python captcha 并行运行（不同端口），验证
        ↓
阶段 3: 修改 core/init.txt，将 captcha/_run.sh 替换为 captcha2/_run.sh
        ↓
阶段 4: 生产切换，观察 1 周无异常后删除 Python captcha 目录
```

### 7.2 gateway 零改动保证

- pymsg 协议不变（JSON RPC 约定不变）
- 监听地址不变（`127.0.0.1:8009`）
- `captcha.ini` 配置不变
- gateway 的 `verify_captcha_remote()` 和 `handleCaptchaImage()` 通过 pymsg `MsgClient` 调用，与 Python 版字节级兼容

### 7.3 回滚方案

- `init.txt` 中恢复 `bash /opt/micro_servers/captcha/_run.sh`
- Python venv 保持不变（不删除），可立即回滚

---

## 8. 安全考量

### 8.1 审计遗留修复

| 编号 | 问题 | 修复方式 |
|------|------|----------|
| NEW-1 | `generate_text()` 使用 `random.choices` 非安全随机 | C++ 版使用 `std::mt19937` 以 `std::random_device` 种子，Token 使用 256-bit `random_device` 直接生成 |
| NEW-2 | 无生成速率限制，可被用于 CPU 资源消耗攻击 | 新增滑动窗口限流（默认 60 次/分钟，可配 `rate_limit_per_min`） |

### 8.2 已有安全机制保持

| 机制 | Python 版 | C++ 版 |
|------|----------|--------|
| Token 一次性使用 | `captcha_store.pop(token)` | `store_.erase(it)` 后返回 |
| 5 分钟过期 | 惰性清理 | 惰性清理（与 Python 版一致） |
| 常数时间比较 | XOR 逐字符 | XOR 逐字符（与 Python 版一致） |
| 仅监听 127.0.0.1 | `RpcServer("127.0.0.1", ...)` | 同 |
| 容量上限 | `MAX_STORE_SIZE = 10000` | 同 |
| PNG base64 编码 | `base64.b64encode` | 手写或 libcrypt `Base64` |
| 配置白名单合并 | `_merge_ini` 仅覆盖已知 key | `CaptchaConfig` 仅解析已知字段 |
| 字体缺失回退 | Pillow `load_default()` | 尝试默认路径 → 失败则退出（快速失败优于静默降级为无抗锯齿位图字体） |

### 8.3 RpcServer 安全特性（继承自 libmsg）

- JSON 解析嵌套深度限制 32 层
- 错误信息脱敏（不泄露内部路径/类型名）
- `recv_timeout` 防 Slowloris（默认 10s）
- Handler 异常不导致服务崩溃

---

## 9. 新增文件清单

```
captcha2/
├── src/
│   └── main.cpp                 # ~420 行，全部实现
├── inc/
│   ├── stb_image_write.h        # 公共领域单头 PNG 编码器（直接引入）
│   └── LogC.hpp                 # ~60 行，简化版异步日志客户端
├── Makefile                      # ~30 行
├── _set.sh                       # make + install（~10 行）
├── _run.sh                       # 启动脚本（~5 行）
└── README.md                     # 模块文档
```

**0 个新增文件需修改现有代码。** gateway / core / waf / init.txt 均零改动。

---

## 10. 依赖清单

| 依赖 | 用途 | 是否新增 | 安装方式 |
|------|------|----------|----------|
| `libus_msg.so` (libmsg) | RPC 服务端 + 异步日志 | 否 | 已通过 `_inst.sh` 安装 |
| `libboost_json` | JSON 解析 | 否 | 已通过 `_setup.deb.sh` 安装 |
| `libboost_program_options` | 命令行参数 | 否 | 已通过 `_setup.deb.sh` 安装 |
| `libfreetype-dev` | TrueType 字体渲染 | **是** | `apt install libfreetype-dev`（已在 `_setup.deb.sh` 中添加） |
| `stb_image_write.h` | PNG 编码 | 形式上是 | 单头文件直接放入 `inc/`，无编译依赖 |

### 10.1 `_setup.deb.sh` 新增依赖

在根目录 `_setup.deb.sh` 的 `apt install` 命令中追加 `libfreetype-dev`：

```bash
sudo apt install -y ... libfreetype-dev
```

该包在 Ubuntu 24.04 中预装，大小约 1.2MB（含 `.so`）。

---

## 11. 与 Python 版的差异汇总

| 项目 | Python 版 | C++ 版 | 影响 |
|------|----------|--------|------|
| 运行时 | Python 3 + venv + Pillow | 单二进制 `captcha2.bin` | 消除 Python 依赖 |
| 随机数 | `random.choices` + `secrets.token_hex(16)` | `mt19937(random_device)` + 256-bit random_device | 安全增强 |
| 速率限制 | 无 | 滑动窗口 60 次/分钟（可配） | 审计 NEW-2 修复 |
| 字体回退 | Pillow `load_default()`（无抗锯齿位图字体） | 快速失败（打印错误退出） | 部署时确保字体安装 |
| Token 长度 | 128-bit hex (32 字符) | 256-bit hex (64 字符) | 对调用方透明（Token 只做 key 用） |
| log_client | Python `AsyncRpcQueue` | C++ `AsyncQueue<LogEntry>` | 功能等价 |
| 进程模型 | Python GIL | 原生多线程（`workers` 配置） | 真实并发图片生成 |

---

## 12. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| FreeType 与 Pillow 字体渲染差异大，验证码视觉效果不一致 | 中 | 中 | P1 阶段人工对比 + 统计特征验证；必要时调整旋转/位置参数 |
| 像素旋转合成逻辑有 bug，字符变形/丢失 | 低 | 高 | 大量单元测试 + 固定种子生成保存图片对比 |
| FreeType 字体缺失导致进程退出 | 低 | 高 | `_setup.deb.sh` 确保 `fonts-dejavu-core` 安装；启动时显式检查字体文件存在 |
| gateway 联调时 pymsg 协议兼容性问题 | 极低 | 高 | libmsg 已在 db2/dnfs2/fsys 中经过充分验证；协议极简 |
| `max_store` 容量保护逻辑与 Python 版不一致 | 低 | 低 | 黑盒测试覆盖边界条件 |

---

## A. 附录：stb_image_write.h 许可说明

`stb_image_write.h` 是 Sean Barrett 编写的单头文件公共领域库（public domain / MIT），广泛用于游戏引擎和图形工具。将其放入 `captcha2/inc/` 目录即可使用，无需额外安装。文件约 1800 行，提供：

```c
// 主要 API
STBIWDEF int stbi_write_png(char const *filename, int w, int h, int comp, const void *data, int stride);
STBIWDEF int stbi_write_png_to_func(stbi_write_func *func, void *context, int w, int h, int comp, const void *data, int stride);
```

本项目使用 `stbi_write_png_to_func`（内存输出），在 `main.cpp` 中：

```cpp
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"
```

---

## 13. 实现后记 · 审计与修复

### 13.1 实现完成

2026-08-09，captcha2 按本方案完成全部开发（P0~P6），编译为 434KB 动态链接 ELF 二进制。
目录结构：

```
captcha2/
├── Makefile                    # g++ -std=c++20 -O2
├── _run.sh / _set.sh           # 启动/编译脚本
├── inc/
│   ├── LogC.hpp                # 异步日志客户端（AsyncQueue → db2:8005）
│   └── stb_image_write.h       # PNG 编码（1724 行，public domain）
├── src/
│   └── main.cpp                # 主程序（672 行）
├── test_gen.cpp                # 图片生成独立测试
├── test_rpc.py                 # pymsg RPC 全功能测试（7 项自动化）
└── captcha2.bin                # 编译产物
```

### 13.2 安全审计

同日完成全量安全审计（参见 `docs/security/captcha2_SECURITY_AUDIT_20260809.md`），
发现 11 项问题（P0×1 / P1×1 / P2×3 / P3×6），其中：

| 编号 | 等级 | 问题 | 状态 |
|------|------|------|------|
| P0-1 | 🔴 | 存储答案未小写化，含大写字母的验证码约 90% 永远校验失败 | ✅ 已修复 |
| P2-2 | 🟡 | `::tolower(char)` 对非 ASCII 输入为 UB | ✅ 已修复 |
| P1-1 | 🟠 | 全局速率限制可被单一匿名用户 DoS | ❌ 待修复（需 gateway 层按 IP 限流） |
| P2-1 | 🟡 | 答案生成用 mt19937 非密码学安全随机 | ❌ 遗留（与 Python 版同源） |

### 13.3 P0-1 修复详情

**根因**: Python 原版存储时即小写化答案（`text.lower()`），C++ 初版直接存原始混合大小写文本，
verify 路由仅对用户输入做 lowercase，导致大小写不匹配。

**修复** (`src/main.cpp` L588-597):
```cpp
std::string text = generate_text(g_config.len);
// 小写化存储，与 Python 版行为对齐（大小写不敏感校验）
std::string answer_lc = text;
std::transform(answer_lc.begin(), answer_lc.end(), answer_lc.begin(),
    [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
token = g_store.generate(answer_lc, g_config.ttl, g_config.max_store);
```

同时修复 `::tolower` UB（L633-636），改用 `unsigned char` lambda 包装。

### 13.4 init.txt 集成

`core/init.txt` 已将 `captcha/_run.sh` 替换为 `captcha2/_run.sh`；
`confs/configs/captcha.ini` 已补充 `max_store=10000` 和 `rate_limit_per_min=60` 字段。
