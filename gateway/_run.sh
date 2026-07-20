#!/bin/bash
cd "$(dirname "$0")" || exit
exec ./build/gateway.out -i example.ini #1>/dev/null
