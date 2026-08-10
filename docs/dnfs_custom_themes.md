# dnfs 自定义主题说明

树状笔记（dnfs）支持通过**特殊节点**自定义整个界面的样式：无需改代码、无需重新部署，发布一个节点即可换肤或深度改造界面，随时一键还原。

两类样式节点：

| 类型 | 表单选项 | 能力 | 适用场景 |
|---|---|---|---|
| `theme` | 主题变量 | 仅覆盖 CSS 变量（`:root` 换肤） | 改配色、圆角、阴影，最稳妥 |
| `style` | 界面样式 | 注入任意 CSS | 改布局、加特效、深度定制 |

---

## 1. 快速上手

### 发布步骤

1. 登录后打开「树状笔记」，进入自己的目标节点（样式节点发布在**谁拥有**的节点下，权限见下）
2. 在「节点操作」表单中，将**节点类型**选为 `主题变量` 或 `界面样式`
3. 在「节点内容」中填写变量或 CSS（格式见后文）
4. 可选：勾选「离开本节点后仍保持生效」（`persist`）
5. 提交——内容经 AI 审核通过后即时生效

### 发布权限

- **节点拥有者或管理员**可发布样式节点（后端强制，前端面板仅做展示切换）
- 在他人节点下提交样式节点会被拒绝（HTTP 403）
- 根层（pid=0）无拥有者，仅管理员可在根层发布
- 所有样式节点内容都会经过 **AI 内容审核**

### 生命周期

| 模式 | 生效范围 | 移除时机 |
|---|---|---|
| 默认（节点作用域） | 进入该节点所在层时生效 | 切换/返回节点时自动还原 |
| `persist`（勾选保持） | 一旦访问即全局保持，跨节点生效 | 用户面板点「还原界面样式」，或停用对应节点 |

- 每个样式节点卡片上有「停用样式/启用样式」与「审计源码」按钮，可逐个控制
- 用户面板的「**还原界面样式**」一键清除全部注入样式（含 persist），本会话内 persist 样式不再自动注入（刷新页面恢复）

---

## 2. theme 节点（主题变量）

### 格式

「节点内容」中**每行一个变量**，分号可选：

```
--accent: #e8853a
--bg: #0a0f0a;
```

### 规则（后端强制）

- 变量名：必须以 `--` 开头，仅含字母/数字/`-`/`_`，长度 ≤ 64
- 变量值：非空，长度 ≤ 256，**不得包含 `{` `}` `<` `>`**（防止逃逸 `:root` 上下文注入任意规则）
- 条数：1 ~ 64 条
- 未列出的变量保持站点默认值，可只覆盖关心的几项

### 可覆盖变量清单

默认值（亮色 / 系统暗色模式）：

| 变量 | 亮色默认 | 暗色默认 | 作用 |
|---|---|---|---|
| `--bg` | `#fdf6ee` | `#1e1712` | 页面背景 |
| `--fg` | `#4a3728` | `#f0e4d8` | 主文字 |
| `--fg-secondary` | `#8c7260` | `#a08c78` | 次要文字 |
| `--card` | `#ffffff` | `#2a2119` | 卡片背景 |
| `--card-hover` | `#fff7ef` | `#342a20` | 卡片悬停背景 |
| `--border` | `#f0e0cc` | `#3e3228` | 边框 |
| `--border-light` | `#f7ecdd` | `#342a20` | 浅边框 |
| `--muted` | `#b89b7a` | `#8c7a66` | 弱化元素 |
| `--accent` | `#e8853a` | `#e8934a` | 品牌强调色 |
| `--accent-dark` | `#cf6b1f` | `#f0a060` | 强调色深阶 |
| `--accent-soft` | `rgba(232,133,58,.10)` | `rgba(232,147,74,.12)` | 强调色底衬 |
| `--accent-glow` | `rgba(232,133,58,.25)` | `rgba(232,147,74,.20)` | 强调色辉光 |
| `--error` / `--error-bg` | `#d94f3d` / `#fef0ee` | `#e8685a` / `#3a2220` | 错误 |
| `--success` | `#5a9e6f` | `#6fb87f` | 成功 |
| `--warning` | `#d4922a` | `#e0a84a` | 警告 |
| `--shadow-sm` / `--shadow-md` | 浅棕阴影 | 深黑阴影 | 投影 |
| `--radius-sm` / `--radius-md` / `--radius-lg` | `10px` / `14px` / `20px` | 同左 | 圆角 |

> 站点会随系统 `prefers-color-scheme` 切换亮/暗两套默认值；theme 节点注入的变量对两种模式**同时生效**（注入的 `:root` 规则优先级高于媒体查询默认值）。

---

## 3. style 节点（任意 CSS）

### 格式

「节点内容」直接填写 CSS 文本，上限 256KB。

### 安全边界

- CSS 无法执行脚本，风险面仅为界面外观；滥用可随时一键还原
- js 特效节点仍运行在 iframe 沙箱中，与样式节点互不影响

### 常用选择器参考

| 选择器 | 对应元素 |
|---|---|
| `.box` | 页面主容器（宽 90%，max-width 720px） |
| `.card` | 节点卡片（含 `:hover`） |
| `.card h3` | 卡片标题（点击进入子节点） |
| `.card-owner` / `.owner-avatar` / `.owner-name` | 发帖人头像与昵称 |
| `.card-stats` / `.stat-item` | 观看/点赞/分享统计 |
| `.card-actions` / `.act-btn`（`.act-edit` `.act-move` `.act-del`） | 卡片操作按钮 |
| `.searchbox` / `.searchbox-input` / `.searchbox-btn` | 顶部搜索框 |
| `.userpanel` / `.minimap` / `.movebar` | 用户面板 / 右侧导航 / 底部移动条 |
| `.tag` | 导航标签 |
| `.node-form` | 底部节点编辑卡 |
| `.sp-code` / `.sp-actions` / `.sp-note` | 特殊节点卡片内元素 |
| `.footer` | 页脚 |
| `body::before` / `body::after` | 点阵底纹 / 顶部暖光（可被覆盖改背景效果） |

### 编写注意事项

- **层叠上下文**：全屏覆盖层建议 `z-index ≤ 9998`（`9999` 被 js 特效 iframe 占用）
- **不拦截交互**：覆盖层务必加 `pointer-events: none`
- **避免动画 `transform`** 作用于卡片祖先：会使 `position: fixed` 后代（如 minimap、特效）定位异常
- 修改 `--accent-glow` 等含 `rgba()` 的值在 theme 节点中是允许的（不含禁字符）

---

## 4. 示例

### 4.1 极客终端风

先发 theme 节点（磷光绿配色）：

```
--bg: #0a0f0a
--fg: #33ff66
--fg-secondary: #1f9944
--card: #0d140d
--card-hover: #111a11
--border: #1a331f
--border-light: #142614
--muted: #2a6b3a
--accent: #33ff66
--accent-dark: #00cc44
--accent-soft: rgba(51, 255, 102, 0.08)
--accent-glow: rgba(51, 255, 102, 0.25)
--error: #ff4444
--error-bg: #2a0d0d
--success: #33ff66
--warning: #ffaa00
```

再发 style 节点（CRT 质感）：

```css
/* 全局等宽字体 */
body, .card, input, textarea, button, select {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Menlo, monospace !important;
}
/* 磷光辉光 */
.card h3, .tag, .searchbox-input {
  text-shadow: 0 0 6px var(--accent-glow), 0 0 2px currentColor;
}
/* 卡片终端窗口化：左侧亮条 + 内发光 */
.card {
  border-left: 3px solid var(--accent);
  box-shadow: 0 0 18px rgba(51, 255, 102, 0.08),
              inset 0 0 40px rgba(51, 255, 102, 0.03);
}
/* 标题加 >_ 提示符 */
.card h3::before {
  content: ">_ ";
  color: var(--accent);
}
/* CRT 扫描线（不拦截点击，z-index 低于特效层 9999） */
body::after {
  content: "";
  position: fixed;
  inset: 0;
  z-index: 9998;
  pointer-events: none;
  background: repeating-linear-gradient(
    0deg,
    rgba(0, 0, 0, 0.18) 0px, rgba(0, 0, 0, 0.18) 1px,
    transparent 1px, transparent 3px
  );
}
/* 链接呼吸辉光 */
a { transition: text-shadow 0.2s; }
a:hover { text-shadow: 0 0 8px var(--accent); }
/* 滚动条终端化 */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--accent); }
```

### 4.2 暗夜蓝（仅 theme，最小改动）

```
--bg: #0d1117
--fg: #c9d1d9
--fg-secondary: #8b949e
--card: #161b22
--card-hover: #1c2129
--border: #30363d
--border-light: #21262d
--muted: #6e7681
--accent: #58a6ff
--accent-dark: #1f6feb
--accent-soft: rgba(88, 166, 255, 0.10)
--accent-glow: rgba(88, 166, 255, 0.25)
```

### 4.3 阅读优化（仅 style，隐藏干扰元素）

```css
/* 隐藏统计与页脚，加宽版心，放大正文字号 */
.card-stats, .footer { display: none; }
.box { max-width: 860px; }
.card p { font-size: 1.05rem; line-height: 1.9; }
/* 卡片去边框，纯排版 */
.card { border: none; box-shadow: none; border-bottom: 1px solid var(--border-light); border-radius: 0; }
```

---

## 5. 常见问题

**发布了但没生效？**
样式节点经 AI 审核，审核未通过会提示「内容审核不通过」；通过后刷新页面生效。非节点拥有者在他人节点下发布会被 403 拒绝。

**界面被改乱了怎么办？**
左上角用户面板 →「还原界面样式」，立即恢复默认。或在对应卡片上点「停用样式」。

**想让全站访客都看到主题？**
发布时勾选「离开本节点后仍保持生效」（persist），并建议放在高层级节点中提高被访问到的概率。

**样式节点之间冲突？**
多个样式节点同时生效时按 DOM 注入顺序叠加，同特异性后者优先；可用卡片上的启停按钮逐个排查。

**存量 image 节点显示异常？**
image 类型已移除，旧图片节点按原始文本显示，拥有者可编辑为 `file` 类型或删除。

---

*相关源码：后端校验 `dnfs2/src/dnfs2.cpp`（`validate_sp`），前端渲染 `fsys/public/js/dnfs.js`（`renderSpCss`）。*
