"""部署版 ONNX 数值验证：输入敏感性 + 与 PyTorch 一致性（重编译前门槛）

用法: venv/bin/python _onnx_validate.py
"""
import sys
sys.path.insert(0, "/opt/blackbird/actpred")

import numpy as np
import torch
from PIL import Image
from torchvision import transforms
import onnxruntime as ort

from config import parse_checkpoint_config
from model import ActionPredictor

CKPT = "checkpoints/pretrain_best_inference.pt"
ONNX = "checkpoints/actpred_deploy.onnx"
IMG_DIR = "data/episode_0000/images"

ck = torch.load(CKPT, map_location="cpu", weights_only=True)
cfg = parse_checkpoint_config(ck["config"])
model = ActionPredictor(cfg.model, cfg.data, pretrained=False)
model.load_state_dict(ck["model_state_dict"])
model.eval()

sess = ort.InferenceSession(ONNX)
in_names = [i.name for i in sess.get_inputs()]
print("ONNX inputs:", in_names)

h, w = cfg.data.image_height, cfg.data.image_width
tf = transforms.Compose([
    transforms.Resize((h, w)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])

imgs_real = [np.array(Image.open(f"{IMG_DIR}/000{i}.jpg").convert("RGB"))
             for i in range(3)]
imgs_zero = [np.zeros_like(imgs_real[0]) for _ in range(3)]

hist_real = np.array([
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 10, 200, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 20, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 0, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 0, 0, 0, 0, 0, 0, 0],
    [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 0, 0, 0, 0, 0, 0, 0, 0],
], dtype=np.float32)
hist_zero = np.zeros((5, 18), dtype=np.float32)

pred_learned = model.pred_token.data.unsqueeze(1).numpy()   # (1,1,1,128)
pred_none = np.zeros((1, 1, 1, 128), dtype=np.float32)


def frames_nhwc(imgs):
    """列表帧 → (1,H,W,3) 归一化 float32 NHWC × frame_len"""
    chw = np.stack([tf(Image.fromarray(im)).numpy() for im in imgs])
    return [chw[i].transpose(1, 2, 0)[None] for i in range(len(imgs))]


def run_onnx(imgs, hist, pred):
    feeds = {f"frame{i}": f for i, f in enumerate(frames_nhwc(imgs))}
    feeds["action_history"] = hist[None, None]
    feeds["pred_token"] = pred
    return sess.run(None, feeds)[0].reshape(-1)


def run_torch(imgs, hist):
    x = torch.stack([tf(Image.fromarray(im)) for im in imgs]).unsqueeze(0)
    hh = torch.tensor(hist).unsqueeze(0)
    with torch.no_grad():
        return model(x, hh).squeeze(0).numpy()


a = run_onnx(imgs_real, hist_real, pred_learned)
b = run_onnx(imgs_zero, hist_real, pred_learned)
c = run_onnx(imgs_real, hist_zero, pred_learned)
d = run_onnx(imgs_zero, hist_zero, pred_learned)
p0 = run_onnx(imgs_real, hist_real, pred_none)

print(f"onnx real_img+real_hist: {np.round(a[:6], 3)}...")
print(f"onnx pred_token=0 diff : max={np.abs(a - p0).max():.4f}")
print(f"img sensitivity  (a-b): max={np.abs(a - b).max():.4f}")
print(f"hist sensitivity (a-c): max={np.abs(a - c).max():.4f}")

ref = run_torch(imgs_real, hist_real)
print(f"torch reference        : {np.round(ref[:6], 3)}...")
print(f"onnx vs torch max_diff : {np.abs(a - ref).max():.6e}")

ok = (np.abs(a - b).max() > 1e-4 and np.abs(a - c).max() > 1e-4
      and np.abs(a - ref).max() < 1e-4)
print("PASS" if ok else "FAIL")
