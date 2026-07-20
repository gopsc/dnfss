#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
# 直接运行Python应用，HTTPS/HTTP配置在app.py中处理
exec fsys.bin --config config.ini 1>/dev/null
