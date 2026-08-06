# blackbird 部署缺陷审查报告（2026-08-06）

> 来源：2026-08-06 首次原生编译部署全流程观察（qsont 运维实录）
> 范围：`_inst.sh` 构建链、systemd 单元、chromadb 模型下载、key 路径约定等部署/运维相关代码缺陷
> 状态标记：🔴 高（影响可靠性） 🟡 中（坑人/影响开发） 🟢 低（文档/非功能）

---

## 🔴 高（影响可靠性）

### 1. Nidus.service 没有 `Restart=` 配置

- **位置**：`core/Nidus.service`
- **问题**：README 声称"崩溃守护由 systemd 负责"，但 service 文件只有 `ExecStart`，systemd 默认 `Restart=no`。
- **影响**：nidus 一旦崩溃不会被拉起，12 个服务全部停摆，且无告警。
- **建议**：加 `Restart=on-failure` + `RestartSec=5`。

### 2. `_inst.sh` 遗漏 3 个模块（已修复 ✅）

- **位置**：`_inst.sh`（本次已修复）
- **问题**：ai_review、aibox、redirect80 不在构建列表，README 只提到 ai_review 需单独安装。
- **影响**：按 README 部署的人会发现 init.txt 引用的服务有 3 个没装。
- **修复**：已补入 `_inst.sh`（confs 之后、core 之前），并更新 README 说明。

### 3. chromadb 模型从 AWS S3 硬编码下载（已修复 ✅）

- **位置**：chromadb 第三方库 `onnx_mini_lm_l6_v2.py`（`MODEL_DOWNLOAD_URL` 写死 S3）
- **问题**：国内直连 18KB/s，90MB 模型要 1 小时+，下载失败直接抛异常导致服务起不来。
- **影响**：ai_review 在国内环境首次启动必挂。
- **修复**：新增 `ai_review/_fetch_onnx_model.sh`，默认从 ModelScope 镜像下载（560KB/s），支持 `ONNX_MODEL_BASE_URL` / `CHROMA_CACHE_DIR` 环境变量覆盖，断点续传 + 幂等；`ai_review/_set.sh` 自动调用。

### 4. key 路径硬编码 + 运行身份不一致

- **位置**：`aibox/_setenv.sh`（读 `~/.cert/deepseek.key`）
- **问题**：Nidus 以 root 运行，root 的 `~` 与 qing 的 `~` 不是同一路径，部署时缺 key 服务启动即崩（RuntimeError），日志难排查。
- **影响**：aibox / ai_review 缺 DeepSeek key 时静默崩溃。
- **建议**：改为环境变量优先，fallback 到 `/root/.cert/` 与 `/home/<user>/.cert/` 多路径探测。

---

## 🟡 中（坑人/影响开发）

### 5. `waf/_gen.sh` 产物与配置不匹配

- **位置**：`waf/_gen.sh`、`waf/config.yaml`
- **问题**：脚本生成 `server.crt`，配置要求 `certs/full_chain.crt`，需手动改名/移动。
- **建议**：脚本直接输出到 `certs/` 目录并命名 `full_chain.crt`。

### 6. pymsg 构建产物属主污染

- **位置**：`pymsg/`（egg-info / build 目录）
- **问题**：`_inst.sh` 以 root 运行，构建产物变成 root 属主，之后 qing 用户再构建报 "Cannot update time stamp"。
- **建议**：构建脚本里 `chown` 回原用户，或明确要求统一以 root 构建。

### 7. core Makefile install 注释掉 `mkdir -p`

- **位置**：`core/Makefile`（`#@mkdir -p $(INSTL_DIRS)`）
- **问题**：依赖目标目录预存在，`/usr/local/bin` 恰好存在所以未炸，属隐藏脆弱点。
- **建议**：取消注释。

### 8. `captcha/_set.sh` 复制粘贴错误

- **位置**：`captcha/_set.sh`
- **问题**：nuitka 注释行写的是 `smtp.py` 而非 `captcha.py`（照抄 smtp）。
- **建议**：修正为 `captcha_service.py`。

---

## 🟢 低（文档/非功能）

### 9. 部署树创建无文档

- **位置**：README 部署章节
- **问题**：`make install` 只装二进制和 systemd 单元，`/opt/micro_servers` 需自行创建，首次部署必踩 127。
- **建议**：README 补充 `mkdir -p /opt/micro_servers` 与同步命令。

### 10. docker-compose 与原生部署并存

- **位置**：`docker-compose.yml`
- **问题**：compose 中 db/ai_review/dnfs 端口与原生部署冲突，切换方式未文档化。
- **建议**：README 说明两种模式互斥，或提供 `docker.sh` 切换脚本说明。

### 11. `.env` 无模板

- **位置**：`ai_review/`
- **问题**：依赖 README 贴示例，无 `.env.example`，漏配一个 key 就是启动崩溃。
- **建议**：提供 `.env.example` 并纳入 git。

---

## 优先级建议

1. **#1 Restart 策略** — 一行 systemd 配置，消除生产事故级隐患
2. **#4 key 路径探测** — 消除部署时静默崩溃
3. **#5-#8** — 开发体验类，随手可改
4. **#9-#11** — 文档类，随 README 更新一起做
