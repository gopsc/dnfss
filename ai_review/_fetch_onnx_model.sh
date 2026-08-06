#!/bin/bash
# ============================================================
# _fetch_onnx_model.sh — 预下载 chromadb ONNX 嵌入模型
# ------------------------------------------------------------
# chromadb 默认从 AWS S3 拉取 all-MiniLM-L6-v2 (onnx.tar.gz)，
# 国内直连极慢(~18KB/s)且失败会导致 ai_review 无法启动。
# 本脚本改为从 ModelScope 镜像逐个下载 chroma 所需的 6 个文件，
# 放到 chromadb 的缓存目录，跳过其内置下载逻辑。
#
# 可配置：
#   ONNX_MODEL_BASE_URL   模型仓库 base URL（默认 ModelScope 镜像）
#   CHROMA_CACHE_DIR      chroma 缓存根目录（默认 $HOME/.cache/chroma）
# ============================================================
set -uo pipefail

CACHE_ROOT="${CHROMA_CACHE_DIR:-$HOME/.cache/chroma}"
DEST_DIR="$CACHE_ROOT/onnx_models/all-MiniLM-L6-v2/onnx"
BASE_URL="${ONNX_MODEL_BASE_URL:-https://modelscope.cn/models/AI-ModelScope/all-MiniLM-L6-v2/resolve/master}"

# chromadb ONNXMiniLM_L6_V2._download_model_if_not_exists 检查的文件
FILES=(
    "config.json"
    "model.onnx"
    "special_tokens_map.json"
    "tokenizer_config.json"
    "tokenizer.json"
    "vocab.txt"
)

echo "[fetch-onnx] 目标目录: $DEST_DIR"
mkdir -p "$DEST_DIR"

# 已有完整模型则跳过
need=0
for f in "${FILES[@]}"; do
    [ -s "$DEST_DIR/$f" ] || need=1
done
if [ "$need" = "0" ]; then
    echo "[fetch-onnx] 模型已存在，跳过下载"
    exit 0
fi

fail=0
for f in "${FILES[@]}"; do
    if [ -s "$DEST_DIR/$f" ]; then
        echo "[fetch-onnx] $f 已存在，跳过"
        continue
    fi
    # model.onnx 在仓库 onnx/ 子目录，其余在根目录
    if [ "$f" = "model.onnx" ]; then
        url="$BASE_URL/onnx/$f"
    else
        url="$BASE_URL/$f"
    fi
    echo "[fetch-onnx] 下载 $f <- $url"
    if ! curl -fsSL --retry 3 --connect-timeout 15 -o "$DEST_DIR/$f" "$url"; then
        echo "[fetch-onnx] ⚠ 下载 $f 失败" >&2
        fail=1
    fi
done

if [ "$fail" = "1" ]; then
    echo "[fetch-onnx] ⚠ 部分文件下载失败，ai_review 启动时 chromadb 会尝试原 S3 下载" >&2
    exit 1
fi

echo "[fetch-onnx] ✅ 完成，文件清单："
ls -la "$DEST_DIR"
