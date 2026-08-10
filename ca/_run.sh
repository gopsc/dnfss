#!/bin/bash
# ca 启动脚本
# 配置完全经 CLI 参数传递（v1.1.0 起不再依赖 confs），
# 部署侧由 core/services.yaml 的 args 声明完整传入；
# "$@" 透传允许命令行临时覆盖任意配置项。
cd "$(dirname "$0")" || exit

exec ./build/ca.out "$@"
