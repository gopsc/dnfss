#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
#exec ./fsys.bin --config config.ini 1>/dev/null
exec python3 ./fsys.py --config config.ini 1>/dev/null
