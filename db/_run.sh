#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
exec ./main.dist/main.bin
#exec python3 main.py
