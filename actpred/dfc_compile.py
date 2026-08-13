"""actpred 部署版 ONNX → HEF 编译脚本（Hailo DFC 5.3，仅 x86 Linux）

流程（对应 DFC 5.x CLI）：
    hailo parser onnx   → HAR（网络翻译）
    hailo optimize      → 量化优化后的 HAR（需校准集 npz）
    hailo compiler      → HEF

用法:
    # 1. 导出部署版 ONNX（DFC 友好图结构）
    python export_deploy.py --model checkpoints/pretrain_best_inference.pt
    # 2. 生成校准集（首次）
    python gen_calibration.py --deploy
    # 3. 编译
    python dfc_compile.py

关键点（踩坑记录）：
  - 训练态 ONNX（5D 输入、AdaptiveAvgPool、MultiheadAttention 的 head
    reshape）DFC 无法解析，必须用 export_deploy.py 的改造版图
  - pred_token 必须作为图输入：常量 initializer 会被 DFC 误转置
  - 必须显式 --start/--end-node-names 与 --input-format，且禁用
    onnxsim（本图简化无收益，反而引入 reshape）
  - 校准集键名必须匹配 HN 层名 actpred_deploy/input_layerN
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile


NET_NAME = "actpred_deploy"
START_NODES = ["frame0", "frame1", "frame2", "action_history", "pred_token"]
INPUT_FORMATS = ["NHWC", "NHWC", "NHWC", "NHWC", "NHWC"]


def hailo(*args, venv_bin: str):
    cmd = [os.path.join(venv_bin, "hailo"), *args]
    env = dict(os.environ, PATH=venv_bin + os.pathsep + os.environ["PATH"])
    return subprocess.run(cmd, env=env, check=True)


def har_input_layer_names(har_path: str) -> list:
    """从 HAR（tar 包）中读 hn 文件，按 input_layerN 排序返回层名"""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(har_path) as tf:
            tf.extractall(tmp)
        hn_files = [f for f in os.listdir(tmp) if f.endswith(".hn")]
        with open(os.path.join(tmp, hn_files[0])) as f:
            hn = json.load(f)
    layers = [(name, l) for name, l in hn["layers"].items()
              if l["type"] == "input_layer"]
    layers.sort(key=lambda x: x[0])
    return [name for name, _ in layers]


def check_input_connectivity(har_path: str, input_layers: list):
    """检查 HAR 中输入层是否都有下游边

    输入层被优化断开时编译照常成功，但 HEF 输出恒定值且无任何报错
    （2026-08-13 实测踩坑），必须在编译前拦截。.edges 文件缺失或格式
    无法识别时仅告警不阻断。
    """
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(har_path) as tf:
            tf.extractall(tmp)
        edge_files = [f for f in os.listdir(tmp) if f.endswith(".edges")]
        if not edge_files:
            print(f"WARNING: {har_path} 无 .edges 文件，跳过输入连通性检查")
            return
        with open(os.path.join(tmp, edge_files[0])) as f:
            edges = json.load(f)

    if not isinstance(edges, list):
        print(f"WARNING: .edges 不是列表（{type(edges).__name__}），"
              f"跳过连通性检查")
        return
    adjacency = set()
    for e in edges:
        if isinstance(e, dict) and "from" in e:
            adjacency.add(e["from"])
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            adjacency.add(e[0])
        else:
            print(f"WARNING: 无法识别 .edges 条目格式: {e!r}，跳过连通性检查")
            return
    disconnected = [n for n in input_layers if n not in adjacency]
    if disconnected:
        sys.exit(
            f"错误: {har_path} 中输入层无下游边（输入被优化断开，"
            f"编译出的 HEF 将输出恒定值）: {disconnected}")
    print(f"输入连通性检查通过: {len(input_layers)} 个输入层均有下游边")


def main():
    parser = argparse.ArgumentParser(description="Compile actpred deploy ONNX to HEF")
    parser.add_argument("--onnx", default="checkpoints/actpred_deploy.onnx")
    parser.add_argument("--calib-npz", default="data/calibration_deploy.npz")
    parser.add_argument("--output", default="checkpoints/actpred_pretrain.hef")
    parser.add_argument("--hw-arch", default="hailo10h",
                        choices=["hailo15h", "hailo15m", "hailo15l",
                                 "hailo10h", "hailo10p"])
    parser.add_argument("--venv-bin", default="venv_dfc/bin",
                        help="安装了 hailo-sdk 的 venv bin 目录")
    args = parser.parse_args()

    venv_bin = os.path.abspath(args.venv_bin)
    if not os.path.exists(os.path.join(venv_bin, "hailo")):
        sys.exit(f"错误: {venv_bin}/hailo 不存在，请先安装 hailo-sdk")

    workdir = tempfile.mkdtemp(prefix="dfc_")
    har_raw = os.path.join(workdir, "parsed.har")
    har_opt = os.path.join(workdir, "optimized.har")
    hef_dir = os.path.join(workdir, "hef")
    os.makedirs(hef_dir)

    try:
        print("[1/3] parser: ONNX → HAR")
        hailo("parser", "onnx", args.onnx, "--hw-arch", args.hw_arch, "-y",
              "--har-path", har_raw,
              "--start-node-names", *START_NODES,
              "--end-node-names", "predicted_action",
              "--input-format",
              *[f"{n}={f}" for n, f in zip(START_NODES, INPUT_FORMATS)],
              "--disable-onnx-simplifier", venv_bin=venv_bin)

        # 校验未被推荐机制截断：输出层应来自预测头（conv 而非 ew_add）
        names = har_input_layer_names(har_raw)
        if len(names) != len(START_NODES):
            sys.exit(f"错误: HAR 输入层数 {len(names)} != {len(START_NODES)}，解析可能被截断")
        check_input_connectivity(har_raw, names)

        print("[2/3] optimize: 量化校准")
        hailo("optimize", har_raw, "--hw-arch", args.hw_arch,
              "--calib-set-path", os.path.abspath(args.calib_npz),
              "--output-har-path", har_opt, venv_bin=venv_bin)
        check_input_connectivity(har_opt, har_input_layer_names(har_opt))

        print("[3/3] compiler: HAR → HEF")
        hailo("compiler", har_opt, "--hw-arch", args.hw_arch,
              "--output-dir", hef_dir, venv_bin=venv_bin)

        hef_src = os.path.join(hef_dir, f"{NET_NAME}.hef")
        shutil.copy(hef_src, args.output)
        shutil.copy(har_opt, args.output.replace(".hef", "_optimized.har"))
        print(f"HEF 已保存: {args.output} ({os.path.getsize(args.output)} bytes)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
