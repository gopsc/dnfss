#!/bin/bash
# 构建 WAF 单二进制
set -e
cd "$(dirname "$0")" || exit

if ! command -v go >/dev/null 2>&1; then
    echo "[ERROR] 未找到 go，请先安装 Go 1.21+"
    exit 1
fi

echo "[1/2] 下载依赖"
go mod download

echo "[2/2] 编译"
CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o waf.bin .

echo "完成: $(pwd)/waf.bin"
