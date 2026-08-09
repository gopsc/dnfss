#!/bin/bash
cd "$(dirname "$0")" || exit
sleep 1
exec ./smtp3.bin
