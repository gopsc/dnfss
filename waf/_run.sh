#!/bin/bash
# 运行 WAF（需先 _set.sh 编译）
cd "$(dirname "$0")" || exit
exec ./waf.bin -c config.yaml
