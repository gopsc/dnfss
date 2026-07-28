#!/bin/bash
cd "$(dirname "$0")" || exit
source venv/bin/activate
exec python3 captcha_service.py
