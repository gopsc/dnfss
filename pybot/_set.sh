#!/bin/bash
cd "$(dirname "$0")" || exit

# 运行时零第三方依赖（纯标准库）；venv 仅为开发测试（pytest）
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 运行测试
exec python3 -m pytest tests/ -v
