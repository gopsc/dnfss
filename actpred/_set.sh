#!/bin/bash
cd "$(dirname "$0")" || exit
python3 -m venv venv
source venv/bin/activate

# CPU-only PyTorch (no CUDA dependency)
# 如需 GPU 支持，注释掉下面一行并改用：pip install torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Remaining deps from Tsinghua mirror
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# ONNX 导出依赖（torch>=2.12 的 torch.onnx.export 走 dynamo 路径，onnxscript 为硬依赖）
pip install onnxscript onnxruntime -i https://pypi.tuna.tsinghua.edu.cn/simple

pip check
mkdir -p checkpoints data

# R2-4 防呆：确认装到的是 CPU 版 torch——requirements.txt 含 torch，
# 若被镜像源重装成 CUDA 版（无 GPU 部署会被 CUDA 依赖拖垮）在此拦截
python3 -c "
import torch
assert torch.version.cuda is None, (
    f'检测到 CUDA 版 torch（{torch.__version__}），'
    '请用 _set.sh 的 CPU 源重装: pip install torch torchvision '
    '--index-url https://download.pytorch.org/whl/cpu --force-reinstall')
print('torch CPU build OK:', torch.__version__)
"

# Pre-download MobileNetV2 weights for offline fine-tuning (optional, skip if backbone=simple)
python3 -c "
import torch, sys, os
hub_dir = torch.hub.get_dir()
cache = os.path.join(hub_dir, 'checkpoints', 'mobilenet_v2-b0353104.pth')
if os.path.exists(cache):
    print(f'MobileNetV2 weights already cached: {cache}')
    sys.exit(0)
url = 'https://download.pytorch.org/models/mobilenet_v2-b0353104.pth'
try:
    torch.hub.load_state_dict_from_url(url, model_dir=os.path.join(hub_dir, 'checkpoints'))
    print('MobileNetV2 weights cached to: ' + cache)
except Exception as e:
    print(f'WARNING: MobileNetV2 weights download failed: {e}')
    print('  backbone=simple still works; for mobilenet, manually download from:')
    print(f'  {url}')
    print(f'  and place at: {cache}')
" || echo "WARNING: MobileNetV2 weight pre-download skipped"
