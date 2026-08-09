#!/bin/bash
cd "$(dirname "$0")" || exit
exec ./captcha2.bin "$@"
