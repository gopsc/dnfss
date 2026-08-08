#!/bin/bash
cd "$(dirname "$0")" || exit
python3 -m venv venv
source venv/bin/activate

# CPU-only PyTorch (no CUDA dependency)
# 如需 GPU 支持，注释掉下面一行并改用：pip install torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Remaining deps from Tsinghua mirror
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

mkdir -p checkpoints data
