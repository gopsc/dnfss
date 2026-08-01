#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
#exec ./main.dist/main.bin
sleep 1
exec python3 dbv2.py
