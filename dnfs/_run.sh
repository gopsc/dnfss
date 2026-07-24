#!/bin/bash
cd "$(dirname "$0")" || exit
source ./.env/bin/activate
#exec ./dnfs.bin
exec python3 dnfs.py
