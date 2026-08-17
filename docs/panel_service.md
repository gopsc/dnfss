# 面板微服务（panel）开发文档

> 状态：设计稿 v0.1（开发前评审）
> 关联：[gateway/README.md](../gateway/README.md)、[docs/dnfs_custom_themes.md](dnfs_custom_themes.md)、[core/services.yaml](../core/services.yaml)、[libhttp/README.md](../libhttp/README.md)

## 1. 背景与目标

### 1.1 问题

现有前端页面（fsys 托管的 `index.html` / `dnfs.html` 等）都是"整页应用"：每个页面由单个服务完整提供。当多个服务都想在首页/工作台展示自己的能力时，只能互相改动前端代码或各自开新页面，服务间耦合、发布相互牵连。

### 1.2 目标

新增**面板微服务**（panel），提供一张**背景视图**（工作台/仪表盘）。视图内的元素与控件**不由面板自己实现**，而是由各业务微服务以**前端代码片段**的形式提供，面板负责聚合、渲染与托管：

- 各服务**自持控件代码**（代码维护在各自模块），通过约定协议暴露片段
- 面板**拉取并聚合**片段，以 iframe 沙箱插入背景视图
- 面板只提供统一外壳（布局 / 主题 / 通信 / 身份透传），**不感知控件业务**

### 1.3 非目标（v1）

- 不做控件可视化拖拽编排（v2 候选）
- 不做用户自定义控件 / 第三方插件市场（控件仅来自内部服务，白名单）
- 不做控件代码的 AI 内容审核——控件是内部服务代码而非用户输入，信任边界与 dnfs2 用户样式节点不同（见 5.3）

## 2. 架构设计

### 2.1 拓扑

```
浏览器
  │  https + q4_token Cookie / Authorization
  ▼
waf:443 ──► ca:8443（验签，注入 X-Auth-User / X-Auth-Admin）
              │  Target-Url: http://127.0.0.1:9201
              ▼
           gateway:8445 ──► panel:9201（Proxy.opened 白名单）
                               │
              ┌────────────────┼──────────────────────┐
              │ 内部 HTTP（--providers 白名单）         │ BFF 转发
              ▼                ▼                       ▼
         dnfs2:8006       fsys:9200              其他 provider 服务
       /panel-widget/x   /panel-widget/y
```

- 面板对浏览器只暴露**一个 origin**（面板自己）；控件 iframe 由面板端点在**同一 origin** 下渲染，不存在跨域、自定义头问题（浏览器无法设置 `Target-Url`，因此控件回源统一走面板 BFF，对齐 fsys→dnfs2 的服务端代理先例）。
- 面板加入 `Proxy.opened` 白名单后走 ca 受保护链路：只有登录用户能打开面板；BFF 转发时透传身份头。

### 2.2 核心概念

| 概念 | 说明 |
|---|---|
| Panel | 面板服务本身：背景视图 + 聚合 / 渲染 / 托管 |
| Widget（控件） | 一个可独立渲染的前端单元，由某微服务提供 |
| Provider（提供方） | 拥有控件代码的微服务，实现 `/panel-widget/<id>` 协议 |
| Manifest | 控件描述：元数据 + html/css/js 片段 + 布局 / 主题声明 |

### 2.3 控件生命周期

1. **声明**：服务在自身实现 `GET /panel-widget/<id>`（内部 HTTP）
2. **拉取**：面板按 `--widgets` 清单拉取各 manifest（缓存 + TTL）
3. **聚合**：面板把 manifest 渲染为独立子页面 `/widget/<id>`（内联片段 + 白名单 CSP）
4. **插入**：面板页用 `<iframe sandbox="allow-scripts">` 加载子页面，完成"背景视图 + 控件"组合
5. **通信**：控件与面板用 postMessage 协议交互（就绪 / 调尺寸 / 主题 / 导航）
6. **回源**：控件业务数据经面板 BFF 转发到所属服务（透传身份头）

### 2.4 端口与技术栈

| 项 | 值 | 理由 |
|---|---|---|
| 端口 | 9201 | 与 fsys:9200 相邻的 HTTP 对外段；8xxx 保留给 pymsg/TCP 内部服务 |
| 语言 | C++20 + libhttp | 与 db2 / dnfs2 / fsys / captcha2 生态一致 |
| 日志 | liblogc（`us/LogC.hpp`） | 统一异步推送 db2 Logs 表 |

## 3. 服务接口

### 3.1 对外（浏览器，经 gateway）

| 路由 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 面板页（背景视图 + 控件网格） |
| `/widgets` | GET | 控件清单 JSON（面板缓存内的 manifest 元数据 + 状态），供页面 JS 渲染网格 |
| `/widget/<id>` | GET | 控件子页面（内联 html/css/js），供 iframe 加载；响应带独立 CSP（见 5.1） |
| `/api/w/<id>/<path...>` | 任意 | BFF：转发到 `<id>` 所属 provider，透传身份头 |
| `/health` | GET | 健康检查 |

### 3.2 对 Provider（内部 HTTP 协议）

```
GET /panel-widget/<widget_id>

200 OK, application/json
{
  "id": "dnfs2-minimap",          // 必须与路径一致
  "title": "树状菜单迷你图",       // 面板标题（≤ 64 字符）
  "version": 3,                   // 递增版本，作为面板缓存键
  "size": {"w": 2, "h": 1},       // 网格单位（1 ≤ w,h ≤ 6）
  "themeable": true,              // 是否响应面板主题事件
  "html": "<div class=\"w-root\">…</div>",
  "css": ".w-root { … }",
  "js": "window.wx.ready(); …"
}
```

面板强制校验（失败视为该控件不可用，返回占位）：
- `html` / `css` / `js` 各 ≤ 256KB；`id` 必须与 URL 一致；所有字段必须存在
- 拉取超时 3s；TTL 默认 60s（`--cache-ttl`）
- provider 不可达 / 校验失败 → 占位卡片「控件暂不可用」，**fail-soft**：面板是展示性服务，一个控件失败不拖垮整页（区别于 ai_review 审核类的 fail-closed 语义）

### 3.3 面板配置（core/services.yaml）

```yaml
- name: panel
  cmd: bash /opt/micro_servers/panel/_run.sh
  args:
    - "--instance"
    - "panel"
    - "--log-level"
    - "info"
    - "--host"
    - "127.0.0.1"
    - "--port"
    - "9201"
    # 控件可见清单（由面板控制；未列出的控件不渲染）
    - "--widgets"
    - "dnfs2-minimap;dnfs2-recent;fsys-storage"
    # 提供方映射（白名单：面板只向这些地址拉取 / 转发）
    - "--providers"
    - "dnfs2=http://127.0.0.1:8006;fsys=http://127.0.0.1:9200"
    - "--cache-ttl"
    - "60"
  depends_on: [dnfs2, fsys]
  sleep_before: 0
```

同时修改 `core/configs/gateway.ini` 白名单：

```ini
[Proxy]
opened = http://127.0.0.1:9200;http://127.0.0.1:8006;http://127.0.0.1:5200;http://127.0.0.1:9201;
```

## 4. 控件协议（前端侧）

### 4.1 运行环境约定

控件运行在 `<iframe sandbox="allow-scripts">` 中：

- **无 `allow-same-origin`**：iframe 内为 opaque origin，读不到主页面 DOM / Cookie / localStorage
- 控件只能：渲染自己的片段、发 postMessage、`fetch` 面板同源路径（经 BFF）
- 控件不得假设自己就是 provider 域：所有业务调用走 `fetch('/api/w/<id>/…')`

### 4.2 postMessage 事件

面板 → 控件：

| 事件 | payload | 说明 |
|---|---|---|
| `panel.theme` | `{theme:"dark"\|"light"}` | 主题切换（仅 `themeable: true` 的控件） |
| `panel.user` | `{user:"user@example.com"}` | 当前登录用户（控件需要个性化时） |

控件 → 面板：

| 事件 | payload | 说明 |
|---|---|---|
| `widget.ready` | `{id}` | 控件渲染完成，面板收起 loading |
| `widget.resize` | `{id, w, h}` | 请求调整网格占位（面板限幅在声明 size 的 ±2 内） |
| `widget.navigate` | `{id, url}` | 请求面板同源跳转（url 必须同 origin，面板校验） |
| `widget.toast` | `{id, message}` | 轻提示 |
| `widget.log` | `{id, level, message}` | 前端日志（面板转发 db2 Logs） |

面板侧校验：优先校验 `e.origin === location.origin`；`payload.id` 必须与绑定的 iframe 一致；必要时以 `e.source === iframe.contentWindow` 兜底校验（opaque origin 场景）。

### 4.3 布局与主题

- v1 用 CSS Grid（12 列），按 `manifest.size` 自动流式排布（`grid-auto-flow: dense`），控件间留隙
- 面板提供统一控件外壳（标题栏 = title + 版本 + 刷新按钮），内容区为 iframe
- 主题变量沿用 dnfs2 风格（`--bg` / `--fg` / `--accent` / …），切换时经 `panel.theme` 同步给 themeable 控件

## 5. 安全设计

### 5.1 控件隔离（核心决策）

**用 iframe 子页面端点 `/widget/<id>`，而非 `srcdoc`。** 原因：

- `srcdoc` 文档**继承父页面 CSP**；面板页若保持严格 CSP（`script-src 'self'`），会连带封死控件的内联脚本
- 独立端点可携带**控件文档自己的 CSP**，实现"父页面严格、控件文档自管"的分层

```http
HTTP/1.1 200 OK
Content-Type: text/html
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; sandbox allow-scripts
```

配合 iframe 属性双保险：`<iframe sandbox="allow-scripts" src="/widget/<id>">`。即使 CSP 头缺失，`sandbox` 属性仍保证 opaque origin + 禁止顶层导航。

### 5.2 信任边界

| 边界 | 策略 |
|---|---|
| provider 拉取 | 仅 `--providers` 白名单地址，内部 HTTP；manifest 全字段校验 + 大小上限 |
| 浏览器直连 | 面板在 `Proxy.opened`，必须经 ca 验签；身份头存在性由 gateway 不变量保证 |
| BFF 转发 | 目标仅限该 widget 所属 provider；透传 `X-Auth-User` / `X-Auth-Admin` / `Authorization`，且 strip-then-set 剥离客户端伪造值（对齐 ca 契约） |
| `widget.navigate` | url 必须同 origin（`new URL(u).origin === location.origin`），否则拒绝 |

### 5.3 日志与审计

- 拉取失败 / 超时 / 校验失败 → db2 Logs（warn 级别）
- manifest `version` 变化 → info 日志（审计"谁升级了控件"）
- 明确不做内容审核：控件来自内部服务而非用户输入，信任边界在 provider 白名单处收口（与 dnfs2 用户样式节点区分，如需对用户内容把关仍走 dnfs2 的 ai_review 链路）

## 6. 目录结构（建议）

```
panel/
├── Makefile
├── _set.sh / _run.sh
├── src/
│   ├── main.cpp          # libhttp HttpServer + 路由注册 + 日志
│   ├── widget_store.cpp  # provider 拉取 / manifest 校验 / TTL 缓存
│   └── bff.cpp           # /api/w/<id> 转发（白名单 + 身份头透传）
└── public/
    ├── panel.html        # 背景视图 + 网格外壳
    └── panel.js          # 拉 /widgets → 建 iframe → postMessage 协议
```

## 7. 实现阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 | 骨架：`_set.sh` / `_run.sh`、libhttp 服务、`/health`、静态面板页、widget_store 拉取 + 缓存、`/widgets`、`/widget/<id>` | curl 可见清单与子页面 |
| M2 | 页面 JS：网格渲染、iframe sandbox 加载、postMessage 协议、loading / 占位态 | 浏览器两个示例控件可显示 |
| M3 | BFF 转发 + 主题同步 + `widget.log` → db2 | 控件调通自己后端接口 |
| M4 | 安全加固审计（CSP / 沙箱 / 白名单复核）+ `tests/blackbox/test_panel.py` + 本文档定稿 | 黑盒测试通过 |

## 8. 测试（tests/blackbox/test_panel.py 思路）

- 起一个 mock provider（Python 标准库 HTTP server）提供两个 manifest
- 断言项：`/widgets` 返回清单；`/widget/<id>` 响应含 CSP `sandbox` 头与内联片段；provider 下线后 `/widgets` 状态为不可用占位；BFF 转发透传 `X-Auth-User`；超大 manifest 被拒；未列入 `--widgets` 的控件不渲染

## 9. 风险与取舍

| 取舍 | 说明 |
|---|---|
| iframe 开销 | 每个控件一个 iframe（含独立文档 + CSP），内存与加载略重；换来的是控件间样式/脚本彻底隔离，控件可独立演进 |
| 聚合单点 | 面板是控件编排的单点；控件业务能力仍分布在各自服务，面板故障只影响聚合视图，不影响各服务自身 |
| 无在线编排 | v1 布局静态声明（manifest.size + 面板清单顺序）；拖拽/自定义布局留给 v2 |

## 10. 参考

- gateway 转发白名单与身份契约：[gateway/README.md](../gateway/README.md)
- 注入机制先例（js 特效 iframe 沙箱 / style 主题）：[docs/dnfs_custom_themes.md](dnfs_custom_themes.md)
- 服务注册与 CLI 配置约定：[core/services.yaml](../core/services.yaml)、[ca/README.md](../ca/README.md)（strip-then-set 契约）
- HTTP 服务端库：[libhttp/README.md](../libhttp/README.md)
