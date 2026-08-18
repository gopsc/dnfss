#!/bin/bash
set -e
cd "$(dirname "$0")"
# 配置全部经 CLI 传递（core/services.yaml 的 args 声明），与 db2/ca/captcha2 的 _run.sh 惯例一致
exec ./build/memdb "$@"