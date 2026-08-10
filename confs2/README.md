# confs2 — 统一配置服务（C++20 重写，已归档）

confs 的 C++20 重写实现，基于 libmsg `RpcServer`，协议与 Python 版 confs 完全兼容（`get` / `list` / `reload` / `health`），消费方零改动。

> **状态：已归档**。配置分发逻辑已合并进 `core` 的 `ConfsTask`（`--confs <port>` 启用，如 `./nidus.out -i . --serve 5200 --confs 8010`），不再独立部署。本目录保留为开发存档，开发文档见 [docs/reports/confs2_cpp_rewrite_dev_doc.md](../docs/reports/confs2_cpp_rewrite_dev_doc.md)。

配套黑盒测试：`python3 test_confs2.py`。
