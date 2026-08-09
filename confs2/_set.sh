#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit

make clean 2>/dev/null || true
make
echo "[confs2] 编译完成 → ./confs2.bin"

# 创建 configs 符号链接（指向 ../confs/configs/，配置文件零迁移）
if [ ! -e configs ]; then
    ln -s ../confs/configs configs
    echo "[confs2] 已创建 configs → ../confs/configs 符号链接"
fi
