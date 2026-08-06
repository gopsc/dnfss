#!/bin/bash
cd "$(dirname "$0")" || exit
python3 -m venv venv
#sudo chmod +x .env/bin/activate
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 预下载 chromadb ONNX 嵌入模型（默认 ModelScope 镜像，避免 AWS S3 国内直连超时）
# 可用 ONNX_MODEL_BASE_URL 覆盖镜像源；失败不阻塞安装，但 ai_review 启动时可能需手动处理
bash _fetch_onnx_model.sh || echo "⚠ onnx 模型下载失败，可稍后重跑 bash _fetch_onnx_model.sh"
#nuitka --onefile --jobs=1 dnfs.py

