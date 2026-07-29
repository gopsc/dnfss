#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
#exec ./smtp.bin
exec python3 smtp.py.remote
