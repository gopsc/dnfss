#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
#exec ./smtp.bin
sleep 2
exec python3 smtp_v2.py
