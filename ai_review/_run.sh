#!/bin/bash
cd "$(dirname "$0")" || exit
source ./venv/bin/activate
#exec ./dnfs.bin
exec python3 ai_review.py "$@"

