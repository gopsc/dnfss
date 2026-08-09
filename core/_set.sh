#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit

make clear && make $@

# 创建 configs 符号链接（指向 ../confs/configs/，内嵌 confs 服务配置文件零迁移）
if [ -L configs ] && [ ! -e configs ]; then
    rm -f configs
fi
if [ ! -e configs ]; then
    ln -s ../confs/configs configs
    echo "[core] 已创建 configs → ../confs/configs 符号链接"
fi

make install
