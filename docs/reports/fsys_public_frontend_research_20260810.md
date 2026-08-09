# fsys/public 前端代码研究报告

> 日期：2026-08-10 · 范围：`fsys/public/` 全部前端代码（**不含 `games/` 目录**） · 托管方：fsys（C++20，127.0.0.1:9200）

---

## 一、总览

fsys/public 是 UniServe 框架对外的全部 Web 前端，由 fsys 静态托管，经 `waf:443 → ca:8443 → gateway:8445 → fsys:9200` 链路送达浏览器。整套前端为**零构建、零框架的原生 HTML/CSS/JS**（Vanilla 技术栈），无打包器、无 npm 依赖，第三方库仅两个 vendored 文件（markdown-it 12.0.0、highlight.js 11.9.0）。

### 代码规模（不含 games/）

| 类别 | 文件 | 行数 | 说明 |
|------|------|------|------|
| 自研 JS | 8 个 | ~2,650 | 业务逻辑主体 |
| 第三方 JS | 2 个 | ~9,500 | markdown-it / highlight.js（vendored，不分析） |
| CSS | 8 个 | ~2,370 | 含设计令牌体系 theme.css |
| HTML | 13 个 | ~2,100 | 含 allcolor.html 内联数据 ~400 行 |
| 合计（自研） | 26 个 | **~7,100** | |

### 页面地图

```
public/
├── index.html        社区首页（ landing，Hero + 特性 + 关于 + CTA ）
├── login.html        登录（图形验证码 + 用户条例弹窗 + 第三方 yhdet 快捷登录）
├── register.html     注册（图形验证码 + 邮箱验证码双因子）
├── forgot.html       重置密码（同注册双因子流程，脚本内联）
├── dnfs.html         树状笔记主应用（核心 SPA 式单页）
├── aibox.html        aibox 智能体 Web 客户端（WebSocket，仅管理员）
├── __view.html       目录浏览页（fsys 无 index.html 时的回退页，__ 前缀被 filelist 过滤）
├── allcolor.html     中国传统色彩调色板（纯静态工具页，内联数据）
├── LICENSE.html      用户使用条例（静态文本，供登录页 iframe 弹窗复用）
├── mgmt/             进程管理器（管理员后台，iframe 嵌套 features/*.html）
│   ├── index.html    旧版独立登录页（疑似遗留，见 §六问题 P1）
│   ├── mgmt.html     管理主框架（左侧功能列表 + iframe 加载功能页）
│   └── features/     process(可用) / user / module / log(占位)
├── css/              theme / index / auth / dnfs / view / aibox / codeblock(空)
└── js/               token_manager / validation / index / login / register / dnfs / aibox / view
```

---

## 二、认证体系：HttpOnly Cookie + Token 生命周期管理

整套前端已在 2026-08 完成向 **HttpOnly Cookie（q4_token）** 的迁移，JS 全程不经手 Token，这是本次研究中最重要的一条架构主线。

### 2.1 token_manager.js（119 行，全局基础设施）

被 index / dnfs / aibox 三页引用，做两件事：

1. **定时静默刷新**：通过 `POST /whoami` 取 `created_at + keep_time` 估算过期点（注释明确指出不能用 `iat`——刷新后 iat 不变），在 80% 生命周期处调 `POST /refresh_token`；失败进入 15s 冷却重估，两次刷新最小间隔 60s。
2. **全局 fetch 拦截器**：覆写 `window.fetch`，任何请求收到 401 先自动刷新一次 Token 再重试原请求；`/refresh_token` 自身排除防循环。Cookie 由 gateway `Set-Cookie` 自动更新，重试无需改请求头。

### 2.2 各页登录态处理

| 页面 | 检测方式 | 401 处理 |
|------|----------|----------|
| index | `POST /whoami`，已登录则切换头部按钮组与 CTA 文案 | 静默忽略（首页允许匿名） |
| dnfs | `loadUserPanel()` 拉 `/whoami`，按 `is_admin` 显示特殊节点面板与管理入口 | 仅笔记接口 401 跳 login.html，其他错误原地提示（不再误判凭证失效） |
| aibox | 启动预检 `/whoami`，非管理员仅提示不阻塞 | WS 握手由 gateway 强制 is_admin 兜底 |
| mgmt | `mgmt.html` 内联 `loadUserInfo()`，未登录 1s 后跳 `index.html` | features/process.html 遇 401 弹窗后跳登录 |

### 2.3 注册/登录/重置三流程

- **login**：邮箱 + 密码 + 图形验证码（`GET /captcha/image`，token 走 `X-Captcha-Token` 响应头，图片 blob → ObjectURL，旧 URL 主动 revoke）。无论成败提交后强制刷新验证码（token 一次性消费）。另有 yhdet 开源社区第三方快捷登录入口（外跳 callback）。
- **register**：图形验证码 → `POST /register/send_code` 发 6 位邮箱验证码（10 分钟有效，前端 60s 重发倒计时与服务端冷却对齐，HTTP 429 时也启动倒计时）→ `POST /register` 提交。
- **forgot**：与 register 几乎同构，走 `/reset/send_code` + `/reset_password`；脚本**直接内联在 HTML 里**（339 行中约 290 行是 register.js 的复制改造）。
- **WafTip**（validation.js）：统一识别 WAF 拦截特征（403 + 纯文本 `blocked by WAF`），与 gateway 自身的 JSON 格式 403 区分，三处表单均接入。

---

## 三、dnfs 树状笔记（dnfs.html + dnfs.js 1187 行，核心应用）

### 3.1 导航模型

- 用 `stack` 数组记录节点路径（节点 id 栈，根为 0），点卡片标题 push、返回 pop，配合 `getNode(id)` 拉子节点列表。
- `Target-Url: http://127.0.0.1:8006` 请求头由 gateway 转发到 dnfs2。
- **导航守卫** `confirmLeaveIfDirty()`：表单有未提交内容时拦截导航（明确注释强调必须在改 stack 之前调用，否则栈错位）。
- 右悬浮 **minimap**（当前层节点锚点跳转 + 折叠），左悬浮 **userpanel**（用户名/角色/特效开关/管理入口/退出），窄屏（<768px）默认折叠。

### 3.2 内容渲染管线

- 普通节点：markdown-it（`html: false`）+ highlight.js 代码高亮。
- **特殊节点**（管理员专属，信封格式 `{"_sp":"link|js|image|file", ...}`）：
  - `link`：favicon 经服务端代理 `/api/favicon?url=`（SSRF 防护在 fsys 侧），新标签 `noopener noreferrer`。
  - `js`：可审计/可运行的前端特效，**iframe `sandbox="allow-scripts"` + srcdoc** 隔离执行，不用 eval/new Function（CSP 未开 unsafe-eval）；`<!-- in-node -->` 标记决定挂在卡片内还是 fixed 全屏（注释详细论证了层叠上下文陷阱）；用户面板提供"无特效模式"开关（localStorage 持久化）。
  - `image`：DataURL 直显；`file`：base64 → Blob 触发下载；上传上限 2.5MB（与后端 3.5M base64 字符上限对齐）。
- 权限显隐：发布权限后端强制（is_admin），前端仅按 `can_operate` 字段渲染编辑/移动/删除按钮组。

### 3.3 其他特性

- 当前层实时搜索（标题/owner/内容，信封类排除 base64），ESC 清空，无匹配有提示。
- 节点移动 = pid 变更的 update_note，底部 movebar 二次确认；移动/发布会走后端 AI 审核，期间按钮组禁用防重。
- 互动统计图标（观看/点赞/分享）已就位，但后端 Notes 表无计数字段，恒显示 0（预留）。
- 大量防御性注释记录了历史 bug 修复（空栈取 `stack[-1]` 得 undefined、confirm 取消时 Promise 兜底恢复按钮等）。

---

## 四、aibox Web 客户端（aibox.html + aibox.js 435 行）

- **WebSocket** 经 gateway `[WsProxy]` 反代至 aibox 服务（`/ws/aibox`），协议与 BotOps/aibox `client.py` 完全兼容。
- 完整流式渲染：chunk 增量拼接 + markdown-it 渲染；reasoning 思考区可折叠、结束后默认折叠、超长仅保留末尾 3000 字符；`ai_break` 支持工具调用分段气泡；中断（interrupt）有完整状态收尾。
- 连接韧性：指数退避重连（2s→30s 封顶）、10s 心跳（低于前置代理 proxy_read_timeout）、手动断开抑制重连。
- 任务面板为抽屉式覆盖层，未读角标（99+ 封顶）；本地命令 `/clear /help /connect /disconnect /interrupt`，其余 `/` 命令透传服务端。
- 移动端细节：`enterkeyhint="send"`、`isComposing` 防中文输入法误发、viewport-fit=cover 刘海屏适配。

## 五、__view 目录浏览（__view.html + view.js）

fsys 对无 index.html 目录的回退浏览页（`__` 前缀使其被 `/api/filelist` 过滤，不出现在列表里）。面包屑 + 搜索（`/` 或 Ctrl+F 聚焦、200ms 防抖、ESC 清空）+ 分类统计（目录/文件/视频/音频）+ 网格列表；视频音频跳 `/video/`（fsys Range 206 流式），`?download=true` 触发下载。所有文件名经 `esc()` 转义防 XSS。

## 六、mgmt 进程管理器（管理员后台）

- `mgmt.html`：顶栏用户信息 + 左侧功能列表 + iframe 加载功能页；功能列表中日志页已被注释停用。
- `features/process.html`：**唯一真正可用的功能页**，经 gateway 代理 core:5200，实现进程列表（兼容 boost property_tree 把数组序列化成对象的怪癖）、新建（/exec）、停止（/kill）、401 跳登录。
- `features/user.html` / `module.html` / `log.html`：占位静态页，按钮仅 alert"需要后端 API 支持"。

## 七、CSS 设计体系

- **theme.css（686 行）是全站基座**：完整 Design Tokens（`--bg/--fg/--accent/--error...` 约 24 个变量），`prefers-color-scheme` 暗色全套覆写，暖棕橙品牌色（accent `#e8853a`），统一表单/按钮/提示（tip-red/yellow/green）/卡片/页脚，480px/360px 两档响应式。
- 页面级 CSS 在其上叠加：index.css（landing 各 section + 滚动渐显 + 光标光斑）、auth.css（注册/重置页品牌渐变顶条）、dnfs.css（点阵底纹、卡片逐张入场动画、`prefers-reduced-motion` 降级）、view.css / aibox.css（全屏应用布局覆盖 theme 居中卡片默认）。
- 一致性良好：动效全部尊重 `prefers-reduced-motion`，悬浮面板统一 backdrop-filter 毛玻璃，暗黑模式覆盖完整。

## 八、发现的问题与改进建议

| # | 级别 | 位置 | 问题 |
|---|------|------|------|
| P1 | 中 | mgmt/index.html + mgmt.html | **旧认证体系遗留**：自建 `/api/login` + JS 写 `token=` Cookie（非 HttpOnly q4_token），logout 删的也是 `token` Cookie，与现行认证完全脱节；该登录页应删除或改造，避免成为绕过口 |
| P2 | 中 | js/view.js | 文件名经 `esc()` HTML 转义后仍**拼进 `onclick="navigate('${path}')"` 内联字符串**：`&#39;` 在属性解析时会解码回单引号，单引号文件名可断开 JS 字符串造成注入。建议改 addEventListener + dataset |
| P3 | 低 | register.js / forgot.html | `POST /register`、`/reset_password` 声明 `Content-Type: application/x-www-form-urlencoded` 但 body 实为 JSON 字符串（后端兼容故能用，契约不洁） |
| P4 | 低 | login.js / register.js / forgot.html | refreshCaptcha / startResendCooldown / 校验三件套**复制了三份**，forgot 干脆内联 290 行；建议抽 `js/auth_common.js` |
| P5 | 低 | js/dnfs.js | 非严格模式下 `obj/jsonBody` 等多处未声明即赋值（隐式全局）；`card.id = item.title` 标题重复或含特殊字符时锚点冲突 |
| P6 | 低 | 空文件 | `css/codeblock.css`（0 行，dnfs.html 仍引用）、`mgmt/features/log.css`（0 行） |
| P7 | 低 | mgmt/features/{user,module,log}.html | 占位页无实现，入口仍展示"模块/用户"按钮，建议隐藏或标注 |
| P8 | 提示 | dnfs.js 统计图标 | visit/like/share 前端已渲染但后端无字段，恒为 0 |

## 九、结论

fsys/public 前端在零框架约束下达到了相当高的完成度：**认证设计现代化**（HttpOnly Cookie + 静默刷新 + 401 拦截重试）、**安全细节扎实**（WAF 特征识别、验证码一次性消费、iframe 沙箱特效、SSRF 防护代理、favicon blob 回收）、**可访问性与移动端适配到位**（reduced-motion、enterkeyhint、isComposing、safe-area）。注释质量突出，大量历史 bug 的成因与防御约定都沉淀在代码里。

主要欠账集中在 **mgmt 子系统**（旧认证遗留 + 三个占位页）和**三处表单脚本的复制粘贴**。优先级建议：P1（旧登录页拆除）> P2（view.js 内联事件注入）> P4（公共脚本抽取）> 其余。
