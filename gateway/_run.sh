#!/bin/bash
# gateway 启动脚本
# 配置统一由 confs 服务管理（confs/configs/gateway.ini），
# 启动时从 confs（CONFS_HOST:CONFS_PORT，默认 127.0.0.1:8010）拉取配置。
cd "$(dirname "$0")" || exit

exec ./build/gateway.out "$@"
