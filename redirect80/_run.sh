#!/bin/bash
cd "$(dirname "$0")" || exit
source venv/bin/activate

# 80 端口是特权端口（<1024），需要 CAP_NET_BIND_SERVICE 能力
# 尝试自动设置 cap（失败不阻塞，用户可手动 sudo 或用 systemd AmbientCapabilities）
_python_bin="$(readlink -f venv/bin/python3)"
if command -v setcap &>/dev/null; then
    if sudo setcap 'cap_net_bind_service=+ep' "$_python_bin" 2>/dev/null; then
        echo "[redirect80] 已为 $_python_bin 设置 cap_net_bind_service"
    else
        echo "[redirect80] ⚠ 无法设置 cap_net_bind_service，可能需要 root 运行"
    fi
fi

exec python3 redirect80.py "$@"
