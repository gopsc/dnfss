# 黑盒用户模拟测试

micro_servers 微服务生态的黑盒测试套件。模拟真实用户操作流程，不依赖内部实现，仅通过 HTTP/TCP 公开接口验证各服务行为。

## 设计原则

- **零依赖**: 仅使用 Python 标准库（`http.client`、`json`、`urllib`、`socket`、`argparse`）
- **用户视角**: 每个测试文件模拟一条完整的用户旅程，而非孤立 API 端点
- **无测试框架**: 纯 `assert` + 计数器，输出 `[PASS]`/`[FAIL]`/`[SKIP]`
- **独立可运行**: 每个 `test_xxx.py` 可单独执行，也可通过 `test_runner.py` 统一调度

## 文件结构

```
tests/blackbox/
├── README.md           # 本文档
├── config.py           # 服务地址与端口映射
├── helpers.py          # HTTP/TCP 请求封装 + 断言工具 + 计数器
├── test_runner.py      # 统一入口，--url 自动识别目标服务
├── test_gateway.py     # 用户注册 → 登录 → Token → 代理转发
├── test_captcha.py     # 获取验证码 → 校验 → 一次性消费
├── test_smtp.py        # 发送邮箱验证码 → 校验
├── test_dnfs.py        # 创建笔记 → 查询 → 更新 → 删除（含内容审核）
├── test_db.py          # 日志写入 → 查询 → 统计 → 清理
├── test_ai_review.py   # 内容审核 → 人工反馈纠正
├── test_static.py      # 首页加载 → 文件浏览 → 视频流 → 路径遍历防护
└── test_waf.py         # 正常请求通行 → SQLi/XSS/命令注入/路径遍历/扫描器拦截
```

## 覆盖的用户旅程

### 1. 注册与登录 (gateway + captcha + smtp)
```
用户访问 captcha:8009 获取图形验证码
    → 提交 captcha_token + answer 校验
    → 访问 smtp:8008 发送邮箱验证码
    → 提交 email + code 完成邮箱验证
    → 访问 gateway:8443 POST /register 注册账号
    → 访问 gateway:8443 POST /login 登录获取 Token
    → 使用 Token 通过 gateway 代理访问后端服务
```

### 2. 笔记管理 (dnfs + ai_review + db)
```
已登录用户携带 Bearer Token
    → POST /add_note 创建笔记（内容经 ai_review:8007 审核）
    → POST /get_notes 查询笔记列表
    → POST /update_note 更新笔记（内容重新审核）
    → POST /delete_note 删除笔记
```

### 3. 内容审核 (ai_review)
```
提交违规内容 → 应返回 invalid: true
提交正常内容 → 应返回 invalid: false
提交误判反馈 → RAG 知识库更新
```

### 4. 集中日志 (db)
```
各服务通过 /log/ingest 推送日志
    → /log/query 按条件查询
    → /log/stats 聚合统计
    → /log/purge 按时间清理
```

### 5. 前端静态服务 (static)
```
访问首页 → 返回 login.html
访问文件浏览 → /api/filelist 返回目录结构
访问视频流 → 支持 Range 分段请求
路径遍历攻击 → 返回 403
```

### 6. WAF 安全防护 (waf)
```
正常请求 → 通过代理到达 gateway
SQL 注入载荷 → 返回 403 blocked by WAF
XSS 跨站脚本 → 返回 403
命令注入 → 返回 403
路径遍历 → 返回 403
扫描器 UA → 返回 403
```

## 运行方式

```bash
# 运行全部测试
python3 test_runner.py

# 指定服务地址（自动识别服务类型）
python3 test_runner.py --url http://127.0.0.1:8443

# 运行单个服务测试
python3 test_gateway.py
python3 test_captcha.py
# ... 依此类推

# 列出所有可用测试
python3 test_runner.py --list
```

## 服务端口默认值

| 服务 | 默认端口 | 绑定地址 |
|------|---------|---------|
| gateway | 8443 | 127.0.0.1 |
| static | 9200 | 127.0.0.1 |
| dnfs | 8006 | 127.0.0.1 |
| db | 8005 | 127.0.0.1 |
| ai_review | 8007 | 127.0.0.1 |
| captcha | 8009 | 127.0.0.1 |
| smtp | 8008 | 127.0.0.1 |
| waf | 443 | 0.0.0.0 |

## 注意事项

- 测试依赖服务运行中，请先通过 `core/init.txt` 启动全部服务
- 注册测试会创建真实数据库记录，测试完成后可手动清理
- SMTP 测试需要真实的邮件服务配置，若未配置则相关测试标记为 SKIP
- WAF 测试需要服务监听 443 端口（可能需要 sudo）
- 所有测试设计为幂等：重复运行不会产生副作用累积
