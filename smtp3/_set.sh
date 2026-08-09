#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit

make clean 2>/dev/null || true
make
echo "[smtp3] 编译完成 → ./smtp3.bin"
