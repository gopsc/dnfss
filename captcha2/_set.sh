#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")" || exit

make clean 2>/dev/null || true
make
echo "[captcha2] 编译完成 → ./captcha2.bin"
