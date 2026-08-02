#!/bin/bash
cd "$(dirname "$0")" || exit
source venv/bin/activate
sleep 1
exec python3 captcha_service.py
