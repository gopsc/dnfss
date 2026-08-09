#!/bin/bash
# 与 bot4cj 用法一致：cat scripts/std.mt | ./_run.sh
# 零第三方依赖（纯标准库），直接用系统 python3，无需 venv
cd "$(dirname "$0")" || exit
exec python3 -m pybot "$@"
