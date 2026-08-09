#!/bin/bash
cd "$(dirname "$0")" || exit
sleep 1
exec ./captcha2.bin
