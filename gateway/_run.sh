#!/bin/bash
cd "$(dirname "$0")" || exit
sleep 2
exec ./build/gateway.out -i example.ini #1>/dev/null
