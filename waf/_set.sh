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

# 强制使用本地 toolchain，避免 Go 1.21+ 自动下载新 toolchain 时连 proxy.golang.org 超时
# 如需启用 toolchain 自动下载，注释掉下面这行并确保 GOPROXY 可达
export GOTOOLCHAIN=local
echo "[env] GOTOOLCHAIN=$GOTOOLCHAIN  GOPROXY=$(go env GOPROXY)"

echo "[1/3] 下载依赖并整理 go.sum（go mod tidy）"
time go mod tidy -v

echo "[2/3] 下载到本地缓存（go mod download）"
time go mod download

echo "[3/3] 编译（go build -v）"
time CGO_ENABLED=0 go build -v -trimpath -ldflags "-s -w" -o waf.bin .

echo "完成: $(pwd)/waf.bin"
ls -lh waf.bin
