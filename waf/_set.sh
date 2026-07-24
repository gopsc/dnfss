#!/bin/bash
# 构建 WAF 单二进制
set -euo pipefail
cd "$(dirname "$0")" || exit

if ! command -v go >/dev/null 2>&1; then
    echo "[ERROR] 未找到 go，请先安装 Go 1.21+"
    exit 1
fi

# 设置 GOPROXY 国内镜像（仅当未显式配置时）
source _goproxy.sh

echo "[1/2] 下载依赖（go mod download -x）"
time go mod download -x

echo "[2/2] 编译（go build -v）"
time CGO_ENABLED=0 go build -v -trimpath -ldflags "-s -w" -o waf.bin .

echo "完成: $(pwd)/waf.bin"
ls -lh waf.bin
