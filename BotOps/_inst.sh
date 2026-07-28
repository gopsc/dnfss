#!/bin/bash
# 编译 BotOps 为独立单文件（不运行）：产出 ./botops.bin
# 用法: ./_inst.sh
cd "$(dirname "$0")" || exit
source venv/bin/activate
python3 -m nuitka --standalone --onefile \
    --assume-yes-for-downloads \
    --include-package-data=textual \
    --output-filename=botops.bin \
    app.py
echo "编译完成: ./botops.bin（运行: ./botops.bin [ws://host:port]）"
