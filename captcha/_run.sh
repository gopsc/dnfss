#!/bin/bash
cd "$(dirname "$0")" || exit
python3 captcha_service.py
