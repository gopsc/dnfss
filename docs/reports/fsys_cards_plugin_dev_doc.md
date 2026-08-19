# 热插拔嵌入式前端页面（服务自带卡片）开发计划

- 日期：2026-08-19
- 状态：待评审（仅开发文档，未实施）
- 关联模块：fsys（前端托管）、dnfs2（试点服务）、core/services.yaml、fsys/public（前端页面）

## 1. 背景与目标

### 1.1 问题

社区首页（fsys 托管的 `index.html`）的卡片是**静态写死在 HTML 里的**（`features` 区块、`quick-links` 导航）。新增/下线一个服务入口需要手工改 HTML + CSS + JS 并重启 fsys，不符合微服务"服务自治、即插即用"的定位。

### 1.2 目标

- **卡片由提供服务的微服务自带**：每个服务在自己的 HTTP 端点上暴露 `GET /cards` 卡片清单（清单协议见 §4）。
- **动态插入**：fsys 作为聚合器定期拉取各服务卡片清单，通过 `GET /api/cards` 提供给前端；前端动态渲染，**不重启 fsys、不改页面代码**即可上线/下线卡片。
- **热插拔语义**：
  - 服务上线 → 下一轮聚合周期内卡片自动出现；
  - 服务下线/故障 → 卡片自动移除（其余服务卡片不受影响）；
  - 卡片内容更新 → 前端下一轮刷新自动生效。
- **范围约束**：v1 只做 `link`（跳转卡片）一种类型 + 协议预留扩展字段；`embed`（内嵌 iframe）与 `data`（动态数据卡片）列为 v2 扩展（§12），本计划不实现。

## 2. 现状分析

| 事实 | 位置 | 影响 |
|------|------|------|
| fsys 为 C++20 单文件服务（1309 行），路由模式 `server.get/post(path, handler)` | `fsys/src/fsys.cpp` | 新增 `/api/cards` 只需注册一个 handler + 一个后台聚合线程 |
| 已有服务端到服务端调用先例：`_handle_dnfs_proxy` 用 `HttpClient::post_stream_sync` 调 dnfs2:8006 | `fsys.cpp:1222-1268` | 聚合器拉取上游卡片复用同一 HttpClient 模式 |
| 已有 SSRF 防护工具：`validate_url_ssrf` / `is_private_host` / `curl_write_cb` 大小上限 | `fsys.cpp:401-461, 1062-1098` | 聚合器安全校验可复用 |
| 前端无框架（原生 HTML/CSS/JS），首页有现成卡片网格样式 `.feature-card` | `fsys/public/index.html:97-138`、`css/index.css` | 新卡片区复用视觉风格；`js/index.js` 为原生 fetch 模式 |
| 页面安全头 CSP：`connect-src 'self'; img-src 'self' data: blob:; frame-ancestors 'self'` | `fsys.cpp:498-501` | 浏览器只能同源 fetch `/api/cards`（正合设计）；iframe 嵌入需同源地址 |
| 内部拓扑：浏览器 → waf:443 → ca:8443 → gateway:8445 → fsys:9200；fsys 可直接访问 127.0.0.1 内部服务 | `core/configs/gateway.ini` | 聚合发生在 fsys 服务端（内部直连，无 CORS 问题）；浏览器只访问 fsys 同源端点 |
| dnfs2 为 C++20 HTTP 服务（libhttp），接口与 fsys 同风格 | `dnfs2/` | 作为首个自带卡片的试点服务最合适 |

## 3. 总体设计

```
┌──────────┐   GET /cards     ┌──────────────┐  GET /api/cards   ┌────────────┐
│  dnfs2   │ ───────────────▶ │     fsys     │ ◀──────────────── │   浏览器    │
│ (:8006)  │  卡片清单 JSON    │  CardRegistry │    合并后卡片 JSON  │  (首页)    │
└──────────┘                  │  轮询线程(30s)│                   └────────────┘
┌──────────┐   GET /cards     │  校验+缓存    │
│  ai_review│ ───────────────▶│              │   js/cards.js 动态渲染
│ (:8007)  │                  └──────────────┘
└──────────┘                      ▲
                              --card-sources 白名单配置
```

要点：

1. **服务自带**：每个参与服务实现 `GET /cards`，返回自己的卡片清单（§4）。卡片声明在服务侧，与服务共存亡——这就是"热插拔"的载体。
2. **fsys 聚合**：fsys 新增 `CardRegistry`（内存缓存 + 后台轮询线程），从 `--card-sources` 白名单中的 URL 定时拉取，逐源校验后合并；对外提供 `GET /api/cards`。
3. **前端动态渲染**：首页新增"服务入口"卡片区，`js/cards.js` 拉取 `/api/cards` 渲染；定时刷新实现"无感热更新"。
4. **安全边界**：上游 URL 仅允许配置白名单（内部 127.0.0.1）；响应大小/超时/类型强校验；卡片字段白名单校验；前端 DOM API 渲染防 XSS。

## 4. 卡片清单协议（cards.json v1）

### 4.1 服务端点

每个参与服务提供：

```
GET /cards
```

- 无需认证（内部服务，由 fsys 服务端直连；与 `/health` 同级）。
- 成功返回 `200 application/json`，体为 4.2 节格式。
- 服务不可用时返回非 2xx 或超时——由 fsys 聚合器视为"该源下线"（§6.4）。

### 4.2 响应格式

```json
{
  "service": "dnfs2",
  "version": 1,
  "cards": [
    {
      "id": "dnfs-tree",
      "title": "树状笔记",
      "description": "以树状结构组织知识的笔记社区",
      "icon": "/__icons/editor.html.ico",
      "url": "/dnfs.html",
      "type": "link",
      "badge": "",
      "order": 0,
      "theme": "auto"
    }
  ]
}
```

### 4.3 字段规范

| 字段 | 必填 | 约束 |
|------|------|------|
| `service` | 是 | `[a-z0-9-_]{1,32}`，服务标识 |
| `version` | 是 | 固定为 `1`（协议版本） |
| `cards[]` | 是 | 数组，单源上限 16 张 |
| `cards[].id` | 是 | `[a-z0-9-_]{1,64}`，源内唯一 |
| `cards[].title` | 是 | ≤ 64 字符 |
| `cards[].description` | 否 | ≤ 200 字符 |
| `cards[].icon` | 否 | 同源相对路径（`/` 开头，≤ 256 字符）或 `data:image/...` base64（≤ 8KB），不允许 http(s) URL（防第三方跟踪/SSRF 面） |
| `cards[].url` | 是 | **同源相对路径**（`/` 开头）或 `https://` 公网 URL；不允许 `http://`、不允许私有 IP（浏览器侧直连，防中间人 + 内部端口探测） |
| `cards[].type` | 是 | 枚举 `link`（v1）；`embed`/`data` 预留（v2，§12） |
| `cards[].badge` | 否 | ≤ 16 字符，角标文本（如"在线"/"beta"） |
| `cards[].order` | 否 | 整数，升序排序，缺省 0 |
| `cards[].theme` | 否 | `auto`（缺省）/`light`/`dark`，前端可依此微调卡片底色 |

> **为什么卡片 URL 只能是相对路径或 https 公网地址**：卡片最终由浏览器点击跳转。浏览器页面来自 `https://<host>:443`（waf 链路），无法直连 `127.0.0.1` 内部端口；内部页面入口（如 dnfs2）已有 fsys/gateway 对外代理路径，卡片直接指向对外可达地址即可。

## 5. fsys 聚合器设计

### 5.1 新增配置（沿用 CLI > 环境变量 > 默认值）

| 配置项 | CLI | 环境变量 | 默认 |
|--------|-----|----------|------|
| 卡片源白名单 | `--card-sources`（分号分隔 URL，如 `http://127.0.0.1:8006/cards`） | `CARD_SOURCES` | 空（关闭聚合，`/api/cards` 返回空列表） |
| 轮询间隔 | `--card-refresh`（秒） | `CARD_REFRESH` | 30 |
| 单源超时 | 复用 `--db-timeout` 风格？——新增 `--card-timeout`（秒） | `CARD_TIMEOUT` | 3 |
| 单源响应上限 | 常量 | — | 256 KB（`kCardMaxBytes`） |

配置校验：`--card-sources` 中的每个 URL 必须通过 `validate_url_ssrf` 校验（仅 http(s)、非私有 IP——此处语义为"仅允许本机内部源"，与 favicon 代理的语义一致但方向相反：**白名单模式**，配置里写了什么才拉什么，且解析后仍须落在 127.0.0.1/::1）。

`core/services.yaml` 的 fsys 条目同步追加 args（示例）：

```yaml
- "--card-sources"
- "http://127.0.0.1:8006/cards"
- "--card-refresh"
- "30"
```

### 5.2 CardRegistry（内存缓存 + 后台线程）

新增 `class CardRegistry`，结构：

```
struct SourceState {
    std::string url;                 // 配置的源 URL
    std::string service;             // 最近一次成功响应的 service 字段
    std::vector<Card> cards;         // 最近一次成功拉取的卡片（已校验）
    std::string error;               // 最近一次失败原因（空=正常）
    std::chrono::steady_clock::time_point fetched_at;
    int consecutive_failures;        // 连续失败计数
};

class CardRegistry {
    void start();                    // 后台线程：立即拉一轮，之后按 refresh 间隔循环
    void stop();
    json::object snapshot();         // 合并所有源的 /api/cards 响应（带锁）
private:
    void poll_once();                // 遍历源，逐一拉取+校验
    std::optional<SourceState> fetch_source(const SourceState&);  // 单源拉取
    std::mutex _mutex;
    std::map<std::string, SourceState> _sources;
};
```

- 后台线程用 `std::condition_variable` + 超时等待实现定时间隔（对齐现有 `stop_watcher` 的停止模式：`stop()` 置标志 + notify，线程退出）。
- 聚合逻辑与 fsys 主体解耦，`_handle_cards` 只调 `snapshot()`，不阻塞请求线程。
- **首次拉取**：`start()` 时立即执行一轮（不等第一个间隔），保证部署后首页马上有卡片。

### 5.3 单源拉取与校验（poll_once 内逐源执行）

1. **拉取**：`HttpClient` GET 源 URL，超时 `--card-timeout`，响应体上限 256KB（沿用 `curl_write_cb` 式上限中断或 `set_max_response_size`）。
2. **类型校验**：响应码必须 200，`Content-Type` 必须 `application/json`（或 `application/json; charset=...`）。
3. **结构校验**（用 boost::json 解析，任何一项失败 → 该源本轮视为失败）：
   - `service` 匹配 `[a-z0-9-_]{1,32}`，且须与源配置的期望服务名一致（若配置形如 `dnfs2=http://...`，见 5.4）；
   - `version == 1`；
   - `cards` 为数组且 ≤ 16；
   - 逐卡片执行 §4.3 字段校验（id 正则、长度、url 仅相对路径或 https 公网、icon 仅相对路径或 data:、type ∈ {link}）。
4. **更新状态**：成功 → 替换 `cards`，清空 error，`consecutive_failures = 0`；失败 → 保留上一轮成功缓存（**缓存降级**），`error` 记录原因，`consecutive_failures++`。
5. **下线判定**（§6.4）：`consecutive_failures >= 3`（即连续失败约 90 秒）→ 该源标记 `down`，前端不再显示其卡片；成功恢复后立即重新显示。

### 5.4 源白名单配置格式

两种写法（推荐前者）：

```
--card-sources "http://127.0.0.1:8006/cards"
--card-sources "dnfs2=http://127.0.0.1:8006/cards"
```

带 `name=` 前缀时强制校验响应 `service` 字段一致；不带前缀时以响应中的 `service` 字段为准。两者都不允许指向非 127.0.0.1/::1 的地址（解析后校验，防配置错误打到外网）。

### 5.5 GET /api/cards 响应

```json
{
  "updated_at": "2026-08-19T10:00:00Z",
  "sources": [
    { "service": "dnfs2", "status": "ok", "cards": 1, "error": null },
    { "service": "ai_review", "status": "down", "cards": 0, "error": "timeout" }
  ],
  "cards": [
    { "id": "dnfs-tree", "service": "dnfs2", "title": "树状笔记",
      "description": "...", "icon": "/__icons/editor.html.ico",
      "url": "/dnfs.html", "type": "link", "badge": "", "order": 0, "theme": "auto" }
  ]
}
```

- `cards`：所有 `status=ok` 的源的卡片合并，按 `order` 升序（同 order 按 service+id 字典序）。
- `updated_at`：本轮聚合完成时间（UTC ISO 8601）。
- 该端点复用 `make_secured_response`（自动带 CSP 等安全头）；CORS 按现有 `_apply_cors` 处理。

## 6. 热插拔语义（行为契约）

| 场景 | 行为 | 时效 |
|------|------|------|
| 新服务上线（配置了源，服务启动） | 聚合器下一轮拉取成功 → `/api/cards` 出现其卡片 → 前端下一轮刷新渲染 | ≤ refresh + 前端刷新间隔 |
| 服务正常下线（进程退出） | 拉取失败 → 缓存降级 → 连续失败 3 轮后 `status=down`，卡片从前端消失 | ~90s |
| 服务短暂抖动（重启） | 失败期间保留旧卡片（缓存降级），恢复后自动更新 | 抖动期间卡片仍可见 |
| 卡片内容更新（服务改了清单） | 下一轮拉取成功即替换 | ≤ 30s |
| 新卡片加入 / 旧卡片移除 | 随清单整体替换（源内以 id 为准整体覆盖） | ≤ 30s |
| 源配置变更（新增/删除源） | 需重启 fsys（配置静态，v1 不做运行时重载；后续可复用 confs 热更新模式扩展） | 重启时 |

> 前端定时刷新（§7.3）默认 60s；聚合 refresh 默认 30s。端到端可见性 ≈ 90s 内。

## 7. 前端改动

### 7.1 index.html

在 `#features` 区块之后、"关于本站"之前插入新区块：

```html
<section id="service-cards" class="section cards-section">
  <div class="wrap">
    <p class="section-eyebrow">服务入口</p>
    <h2 class="section-title">本站提供的服务</h2>
    <div id="cards-grid" class="cards-grid" aria-live="polite"></div>
  </div>
</section>
```

- 容器初始为空（`cards.js` 负责填充），无 JS / 聚合关闭时该区块高度塌陷或显示占位文案，不影响首屏。
- 页脚引入 `<script src="js/cards.js"></script>`。

### 7.2 css/index.css（或新增 css/cards.css）

- `.cards-grid`：`display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap`，复用 `.feature-card` 的视觉语言（圆角、hover 抬升、深色模式变量）。
- `.svc-card`：链接卡片（`<a>` 整卡可点），含 icon 位、标题、描述、badge 角标。
- 空态样式 `.cards-empty`（"暂无可用的服务卡片"）。

### 7.3 js/cards.js（新增，原生 JS 无依赖）

```js
(function () {
  'use strict';
  const REFRESH_MS = 60 * 1000;   // 与聚合 refresh 协调，见 §6
  const grid = document.getElementById('cards-grid');

  function render(data) { /* 见下 */ }
  async function load() {
    try {
      const res = await fetch('/api/cards');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      render(await res.json());
    } catch (e) {
      console.warn('cards: 拉取失败', e.message);
      // 保留上一帧渲染结果，不闪烁；下一轮重试
    }
  }
  load();
  setInterval(load, REFRESH_MS);
})();
```

- **渲染安全（XSS 防线）**：一律用 `document.createElement` + `textContent` 赋值；禁止 `innerHTML` 拼接任何来自 `/api/cards` 的字段（title/description/badge 均视为不可信）。`icon` 仅允许相对路径（拼 `src` 前校验 `/` 开头）或 `data:image/` 前缀（且整体长度 ≤ 8KB）。`url` 仅允许 `/` 开头相对路径或 `https://` 前缀（与 §4.3 一致，前端二次防御）。
- `target` 处理：相对路径同窗口跳转；https 外链 `target="_blank" rel="noopener noreferrer"`。
- `source.status === 'down'` 的源不渲染其卡片；`cards` 为空时显示空态文案。
- 深色模式：`theme` 字段为 `dark/light` 时给卡片追加类名，由 CSS 微调。

### 7.4 CSP 兼容性核查

- `connect-src 'self'` → `/api/cards` 同源 fetch，通过。
- `img-src 'self' data: blob:` → icon 相对路径与 data: 图，通过。
- iframe（v2 `embed`）→ `frame-ancestors 'self'` 限制的是"本站被嵌入"，不限制本站嵌入同源页面，届时 `embed_url` 须为同源地址；v1 无需改动 CSP。

## 8. 试点服务：dnfs2

作为首个自带卡片的服务，验证协议端到端：

- 在 dnfs2（C++20，libhttp）新增路由 `GET /cards`，返回静态卡片清单：

```json
{
  "service": "dnfs2",
  "version": 1,
  "cards": [
    { "id": "dnfs-tree", "title": "树状笔记",
      "description": "以树状结构组织知识的笔记社区，支持 Markdown 与多媒体分享",
      "icon": "/__icons/editor.html.ico", "url": "/dnfs.html",
      "type": "link", "badge": "在线", "order": 0, "theme": "auto" }
  ]
}
```

- `url: /dnfs.html` 指向 fsys 托管的笔记页（现有对外路径），无需新增对外路由。
- 后续其他服务（ai_review、aibox 等）按同一协议各加一个 `GET /cards` 即可，fsys 侧零改动。

## 9. 安全设计（威胁模型）

| 威胁 | 缓解 |
|------|------|
| 上游源被恶意控制 → 卡片携带恶意内容（XSS） | ① fsys 聚合端字段级校验（id/title/url/icon 白名单正则 + 长度上限）；② 前端 textContent 渲染 + url/icon 前缀二次校验；③ 卡片内容全部视为不可信数据 |
| 恶意源返回超大响应 → 内存 DoS | 单源响应上限 256KB + 超时 3s + 卡片数上限 16/源 |
| 配置被篡改指向外网/内网探测 | `--card-sources` 白名单 + 解析后必须 127.0.0.1/::1（fail-closed 拒绝启动或跳过该源并告警日志）；文件权限收紧（services.yaml 属主 root） |
| 上游服务伪造 service 名 | 带 `name=` 前缀的源强制校验响应 `service` 一致 |
| 聚合器错误导致 fsys 故障 | 单源失败只降级该源，绝不抛异常出请求线程；`snapshot()` 全程锁保护；上游超时由 HttpClient 控制 |
| 卡片 URL 指向内部端口（浏览器 SSRF 面） | 聚合端仅放行 `/` 相对路径与 `https://` 公网；前端二次校验同规则 |
| 前端轮询放大 | 60s 间隔 + 单请求，可忽略 |

## 10. 测试策略

`tests/blackbox/test_static.py` 追加（纯 Python 标准库，沿用现有 helpers）：

1. **聚合正常路径**：`GET fsys:/api/cards` → 200；`sources` 含 `dnfs2` 且 `status=ok`；`cards` 含 `id=dnfs-tree`，字段完整。
2. **协议字段校验**：构造合法 `GET /cards` 假源验证校验逻辑（可用独立测试进程起一个临时 HTTP 服务，指向 127.0.0.1 随机端口，fsys 配置加该源）——校验非法 id、超长 title、非法 url 被剔除，源标记 error。
3. **源下线**：假源停止服务 → 聚合轮询后该源 `status=down`，其卡片不出现；合法源卡片不受影响。
4. **前端静态检查**：`js/cards.js` 存在且 index.html 正确引入（沿用现有静态断言风格）。
5. 回归：现有 test_static.py 全部用例（根路径、文件浏览、Range 视频流）不回归。

## 11. 实施步骤与文件清单

| # | 步骤 | 文件 |
|---|------|------|
| 1 | 协议常量与配置项：新增 `kCardMaxBytes`、`kCardMaxPerSource`、`kCardDownAfter` 等常量；`Config` 增加 `card_sources` / `card_refresh` / `card_timeout`，`load_config` 增加 CLI/env/校验 | `fsys/src/fsys.cpp` |
| 2 | `CardRegistry`：SourceState/Card 结构、`start/stop/poll_once/fetch_source/snapshot`、后台线程 | `fsys/src/fsys.cpp` |
| 3 | 路由注册：`server.get("/api/cards", ...)` + `_handle_cards` handler | `fsys/src/fsys.cpp` |
| 4 | 服务实例化与生命周期：`FsysServer` 构造时建 registry，`run()` 启动、退出时 stop | `fsys/src/fsys.cpp` |
| 5 | 前端容器 + 样式 + 渲染脚本 | `fsys/public/index.html`、`fsys/public/css/index.css`、`fsys/public/js/cards.js` |
| 6 | 试点服务：dnfs2 新增 `GET /cards` 路由（静态清单） | `dnfs2/src/`（对应 main/服务源文件） |
| 7 | 部署配置：fsys 条目追加 `--card-sources` 等 args | `core/services.yaml` |
| 8 | 黑盒测试 | `tests/blackbox/test_static.py`（或新增 `test_cards.py`） |
| 9 | 文档同步：根 README（服务说明表 + 端口表无变化、fsys 节补聚合说明）、本计划状态改为已实施 | `README.md` |

依赖顺序：1 → 2 → 3/4 → 5（前端可并行）→ 6 → 7 → 8 → 9。构建验证：`cd fsys && bash _set.sh`（make 编译通过）、`cd dnfs2 && bash _set.sh`。

## 12. 后续扩展（v2，不在本期）

- **`embed` 卡片**：iframe 内嵌服务自带子页面。需 fsys 提供子路径代理（如 `/card_embed/<service>/<path>`，内部转发到源服务），并给 iframe 加 `sandbox` 属性；CSP 需评估 `frame-src`。
- **`data` 卡片**：卡片带 `data_url`，前端定时拉 JSON 渲染（如"服务状态"卡片）。数据拉取建议经 fsys 代理（浏览器不能直连内部端口），需新增代理端点并做响应校验。
- **源配置热重载**：`--card-sources` 支持运行时刷新（复用 confs mtime 重载模式）。
- **卡片注册中心**：未来若服务数量增多，可考虑由 core 统一登记 `GET /cards` 地址，fsys 从 core 发现，免去手工维护白名单。

## 13. 风险与权衡

- **聚合器是新的常驻后台线程**：逻辑简单（sleep/notify 轮询），崩溃面小；单源故障隔离 + 缓存降级保证主请求路径不受影响。
- **卡片可见性延迟 ~90s**：热插拔非"秒级"，对社区首页场景可接受；如需秒级可后续加 WebSocket 推送（v2）。
- **信任模型**：fsys 信任 `--card-sources` 白名单内的服务所返回的卡片内容（经校验后）。白名单配置属于部署安全边界，与 services.yaml 同级保护（root:root 0640，见根 README 部署安全约定）。
- **不做**：v1 不含卡片管理 UI、不含用户自定义卡片、不含 embed/data 类型——保持最小可用闭环。
