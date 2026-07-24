#!/bin/bash
# 设置 Go 模块代理为国内镜像，加速依赖下载
# 设计要点：
#   1. 既 export 到当前 shell（保证后续 go 命令立即可用）
#   2. 也 go env -w 持久化（保证后续会话可用）
#   3. 用户已自配非默认代理时尊重配置不覆盖
# 用法：source _goproxy.sh  （由 _set.sh 调用，需在同 shell 生效）
set -e

if ! command -v go >/dev/null 2>&1; then
    echo "[skip] 未找到 go，跳过 GOPROXY 设置"
    return 0 2>/dev/null || exit 0
fi

CURRENT=$(go env GOPROXY)
DEFAULT_PROXY="https://proxy.golang.org,direct"

# 用户已显式设置为非默认值（corporate proxy 等），尊重不覆盖，但仍 export 保证当前 shell 一致
if [ "$CURRENT" != "$DEFAULT_PROXY" ] && [ -n "$CURRENT" ]; then
    echo "[skip] GOPROXY 已配置为: $CURRENT"
    export GOPROXY="$CURRENT"
    return 0 2>/dev/null || exit 0
fi

# 用户已用环境变量临时设置，直接用
if [ -n "${GOPROXY:-}" ] && [ "${GOPROXY:-}" != "$DEFAULT_PROXY" ]; then
    echo "[ok] 使用环境变量 GOPROXY=$GOPROXY"
    return 0 2>/dev/null || exit 0
fi

# 切到国内镜像
echo "[setup] 设置 GOPROXY=https://goproxy.cn,direct"
go env -w GOPROXY=https://goproxy.cn,direct
go env -w GOSUMDB=sum.golang.google.cn
# 同时 export 到当前 shell，确保本会话内 go 命令立即生效
export GOPROXY=https://goproxy.cn,direct
export GOSUMDB=sum.golang.google.cn
echo "[ok] GOPROXY=$GOPROXY  GOSUMDB=$GOSUMDB"
