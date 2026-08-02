#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
#exec ./fsys.bin 1>/dev/null
sleep 2
exec python3 ./fsys.py 1>/dev/null
