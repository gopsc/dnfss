#!/bin/bash
cd "$(dirname "$0")" || exit
sleep 1
exec ./build/gateway.out -i example.ini #1>/dev/null
