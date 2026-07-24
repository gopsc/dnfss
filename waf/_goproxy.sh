#!/bin/bash
# 设置 Go 模块代理为国内镜像，加速依赖下载
# 仅当用户未显式设置 GOPROXY 或仍为默认值时修改
set -e

if ! command -v go >/dev/null 2>&1; then
    echo "[skip] 未找到 go，跳过 GOPROXY 设置"
    exit 0
fi

CURRENT=$(go env GOPROXY)

# 已显式设置为非默认值（如用户自配 corporate proxy），尊重用户配置不覆盖
if [ "$CURRENT" != "https://proxy.golang.org,direct" ] && [ -n "$CURRENT" ]; then
    echo "[skip] GOPROXY 已配置为: $CURRENT"
    exit 0
fi

# 优先用环境变量 GOPROXY（临时设置），否则写入 go env 持久化
if [ -n "${GOPROXY:-}" ]; then
    echo "[ok] 使用环境变量 GOPROXY=$GOPROXY"
    exit 0
fi

echo "[setup] 永久设置 GOPROXY=https://goproxy.cn,direct"
go env -w GOPROXY=https://goproxy.cn,direct
go env -w GOSUMDB=sum.golang.google.cn
echo "[ok] 当前 GOPROXY=$(go env GOPROXY)  GOSUMDB=$(go env GOSUMDB)"
