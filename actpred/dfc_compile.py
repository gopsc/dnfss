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
import re
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


def _har_hn_layers(har_path: str) -> dict:
    """解包 HAR 并读取主 hn 文件的 layers（优化产物含 fp/native 变体，取主图）"""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(har_path) as tf:
            tf.extractall(tmp)
        hn_files = [f for f in os.listdir(tmp) if f.endswith(".hn")]
        if not hn_files:
            raise ValueError(f"{har_path} 中无 .hn 文件")
        main = [f for f in hn_files
                if not f.endswith(".fp.hn") and not f.endswith(".native.hn")]
        hn_file = main[0] if main else hn_files[0]
        with open(os.path.join(tmp, hn_file)) as f:
            return json.load(f)["layers"]


def _input_layer_index(name: str) -> int:
    m = re.search(r"input_layer(\d+)$", name)
    return int(m.group(1)) if m else 0


def har_input_layer_names(har_path: str) -> list:
    """从 HAR 主 hn 图读输入层名，按 input_layerN 数字后缀排序"""
    layers = _har_hn_layers(har_path)
    names = [n for n, l in layers.items() if l["type"] == "input_layer"]
    names.sort(key=_input_layer_index)
    return names


def check_input_connectivity(har_path: str, input_layers: list):
    """检查 HAR 图中每个输入层是否可达任一输出层

    输入层被优化断开时编译照常成功，但 HEF 输出恒定值且无任何报错
    （2026-08-13 实测踩坑），必须在编译前拦截。DFC 5.3 的 HAR 不含
    .edges 文件，连通性改从主 hn 图的 layer output 边 BFS 判定。
    """
    layers = _har_hn_layers(har_path)
    adj = {n: l.get("output", []) for n, l in layers.items()}
    targets = {n for n, l in layers.items() if l["type"] == "output_layer"}
    if not targets:
        print(f"WARNING: {har_path} 图中无输出层，跳过连通性检查")
        return
    disconnected = []
    for name in input_layers:
        seen, stack = set(), [name]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        if not (seen & targets):
            disconnected.append(name)
    if disconnected:
        sys.exit(
            f"错误: {har_path} 中输入层不可达输出层（输入被优化断开，"
            f"编译出的 HEF 将输出恒定值）: {disconnected}")
    print(f"输入连通性检查通过: {len(input_layers)} 个输入层均可达输出层")


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
