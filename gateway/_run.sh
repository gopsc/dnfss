#!/bin/bash
# gateway 启动脚本
# 默认从 confs 统一配置服务拉取配置（confs 需已启动）。
# 若需使用本地 ini 文件（开发/调试），传入 -i <path>，例如：./_run.sh -i example.ini
cd "$(dirname "$0")" || exit

# 等待依赖服务启动（confs/db）
sleep 2

exec ./build/gateway.out "$@"
