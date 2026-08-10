#!/bin/bash
cd "$(dirname "$0")" || exit

# 加载 SMTP 凭据（与 Python smtp_v2.py 共用 ../smtp/.env）
# .env 格式：KEY=VALUE（# 开头为注释），set -a 自动 export
ENV_FILE="../smtp/.env"
if [ -f "$ENV_FILE" ]; then
	set -a
	. "$ENV_FILE" 2>/dev/null
	set +a
fi

exec ./smtp3.bin "$@"
