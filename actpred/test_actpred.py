"""actpred 模块单元测试

运行方式：
    cd actpred && python -m pytest test_actpred.py -v
    # 或
    cd actpred && python test_actpred.py
"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image
import torch

from config import (Config, DataConfig, ModelConfig, TrainConfig,
                    build_argparser, apply_overrides, validate_config_values,
                    parse_checkpoint_config)
from dataset import _natural_key, ActionDataset, DataRecorder
from model import ActionPredictor
from mt_parser import parse_mt_action, parse_mt_directory
from predict import ActionPredictorInference, parse_action_history
from pretrain_mt import MTActionDataset


def _write_mt(dir_path, name, content):
    path = Path(dir_path) / name
    path.write_text(content, encoding="utf-8")
    return path


def _make_seq_mt(n_rows):
    """生成 3 列 @Action 块，第 i 行为 [i, i*10, i*100]"""
    lines = ["@Action", "a.A  b.A  c.A;"]
    for i in range(n_rows):
        lines.append(f"{i}  {i * 10}  {i * 100};")
    lines.append("@")
    return "\n".join(lines) + "\n"


class TestNaturalKey(unittest.TestCase):
    """测试自然排序键函数"""

    def test_natural_order_no_padding(self):
        """无前导零的文件名应按数字序排列"""
        files = [Path(f"{i}.jpg") for i in [3, 1, 10, 2, 20]]
        sorted_files = sorted(files, key=_natural_key)
        names = [f.name for f in sorted_files]
        self.assertEqual(names, ["1.jpg", "2.jpg", "3.jpg", "10.jpg", "20.jpg"])

    def test_natural_order_with_padding(self):
        """有前导零的文件名排序也应正确"""
        files = [Path(f"{i:04d}.jpg") for i in [3, 1, 10, 2, 20]]
        sorted_files = sorted(files, key=_natural_key)
        names = [f.name for f in sorted_files]
        self.assertEqual(names, ["0001.jpg", "0002.jpg", "0003.jpg", "0010.jpg", "0020.jpg"])


class TestParseActionHistory(unittest.TestCase):
    """测试动作历史解析函数"""

    def test_single_step(self):
        history = parse_action_history("1,2,3,4", action_dim=4)
        self.assertEqual(history.shape, (1, 4))
        np.testing.assert_array_equal(history[0], [1, 2, 3, 4])

    def test_multi_step(self):
        history = parse_action_history("1,0,0,0:0,1,0,0:0,0,1,0", action_dim=4)
        self.assertEqual(history.shape, (3, 4))

    def test_wrong_dim_raises(self):
        with self.assertRaises(ValueError):
            parse_action_history("1,2,3", action_dim=4)


class TestActionPredictor(unittest.TestCase):
    """测试多帧注意力模型前向传播"""

    def test_forward_multi_frame(self):
        data_cfg = DataConfig(image_width=64, image_height=64, action_dim=18, history_len=5, frame_len=3)
        model_cfg = ModelConfig(cnn_backbone="simple")
        model = ActionPredictor(model_cfg, data_cfg)
        model.eval()

        images = torch.randn(2, 3, 3, 64, 64)
        history = torch.randn(2, 5, 18)
        with torch.no_grad():
            out = model(images, history)
        self.assertEqual(out.shape, (2, 18))

    def test_forward_single_frame_degenerate(self):
        """frame_len=1 退化为单帧输入也应正常前向"""
        data_cfg = DataConfig(image_width=32, image_height=32, action_dim=4, history_len=3, frame_len=1)
        model_cfg = ModelConfig(cnn_backbone="simple")
        model = ActionPredictor(model_cfg, data_cfg)
        model.eval()

        images = torch.randn(2, 1, 3, 32, 32)
        history = torch.randn(2, 3, 4)
        with torch.no_grad():
            out = model(images, history)
        self.assertEqual(out.shape, (2, 4))

    def test_forward_dynamic_batch(self):
        data_cfg = DataConfig(image_width=32, image_height=32, action_dim=4, history_len=3, frame_len=2)
        model_cfg = ModelConfig(cnn_backbone="simple")
        model = ActionPredictor(model_cfg, data_cfg)
        model.eval()

        for batch_size in [1, 4, 8]:
            images = torch.randn(batch_size, 2, 3, 32, 32)
            history = torch.randn(batch_size, 3, 4)
            with torch.no_grad():
                out = model(images, history)
            self.assertEqual(out.shape, (batch_size, 4))

    def test_custom_attn_config(self):
        """自定义 attn_dim/heads/layers 应可构建并前向"""
        data_cfg = DataConfig(image_width=32, image_height=32, action_dim=4, history_len=3, frame_len=2)
        model_cfg = ModelConfig(attn_dim=64, attn_heads=2, attn_layers=1)
        model = ActionPredictor(model_cfg, data_cfg)
        model.eval()

        with torch.no_grad():
            out = model(torch.randn(1, 2, 3, 32, 32), torch.randn(1, 3, 4))
        self.assertEqual(out.shape, (1, 4))


class TestDataRecorder(unittest.TestCase):
    """测试数据录制器"""

    def test_record_and_flush(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DataRecorder(tmpdir, action_dim=4)
            for _ in range(10):
                img = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
                action = np.random.randn(4).astype(np.float32)
                recorder.record(img, action)
            episode_path = recorder.flush()

            self.assertTrue(os.path.exists(os.path.join(episode_path, "actions.npy")))
            images_dir = os.path.join(episode_path, "images")
            self.assertTrue(os.path.isdir(images_dir))
            self.assertEqual(len(os.listdir(images_dir)), 10)

            actions = np.load(os.path.join(episode_path, "actions.npy"))
            self.assertEqual(actions.shape, (10, 4))

    def test_multiple_episodes_increment_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            r1 = DataRecorder(tmpdir, action_dim=4)
            r1.flush()
            r2 = DataRecorder(tmpdir, action_dim=4)
            r2.flush()

            self.assertNotEqual(r1.episode_dir, r2.episode_dir)
            self.assertIn("episode_0001", str(r2.episode_dir))


class TestActionDataset(unittest.TestCase):
    """测试数据集加载"""

    @staticmethod
    def _create_dummy_episode(base_dir, n_frames=10, action_dim=4, image_size=32):
        ep_dir = base_dir / "episode_0001"
        images_dir = ep_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        actions = []
        for i in range(n_frames):
            img = Image.fromarray(
                np.random.randint(0, 255, (image_size, image_size, 3), dtype=np.uint8))
            img.save(images_dir / f"{i:04d}.jpg")
            actions.append(np.random.randn(action_dim).astype(np.float32))
        np.save(ep_dir / "actions.npy", np.array(actions))

    def test_dataset_loading(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_dummy_episode(Path(tmpdir), n_frames=10, action_dim=4)
            ds = ActionDataset(tmpdir, history_len=3, action_dim=4,
                               image_width=32, image_height=32,
                               frame_len=3)
            # 采样起点 max(history_len, frame_len-1) = 3 → 10 - 3 = 7 samples
            self.assertEqual(len(ds), 7)

            sample = ds[0]
            self.assertIn("images", sample)
            self.assertIn("action_history", sample)
            self.assertIn("target", sample)
            self.assertEqual(sample["images"].shape, (3, 3, 32, 32))
            self.assertEqual(sample["action_history"].shape, (3, 4))
            self.assertEqual(sample["target"].shape, (4,))

    def test_frame_len_gt_history_shifts_start(self):
        """frame_len-1 > history_len 时采样起点由帧窗口决定"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_dummy_episode(Path(tmpdir), n_frames=10, action_dim=4)
            ds = ActionDataset(tmpdir, history_len=3, action_dim=4,
                               image_width=32, image_height=32,
                               frame_len=6)
            # 起点 max(3, 5) = 5 → 10 - 5 = 5 samples
            self.assertEqual(len(ds), 5)
            sample = ds[0]
            self.assertEqual(sample["images"].shape, (6, 3, 32, 32))

    def test_episode_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_dummy_episode(Path(tmpdir))
            count = ActionDataset.get_episode_count(tmpdir)
            self.assertEqual(count, 1)

    def test_empty_stats_raises(self):
        """空数据集计算归一化统计量应报错"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                ActionDataset(tmpdir, history_len=5, action_dim=4,
                              normalize_actions=True)

    def test_invalid_stats_path_raises(self):
        """指定的 stats 路径不存在应报错"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                ActionDataset(tmpdir, history_len=3, action_dim=4,
                              normalize_actions=True,
                              action_stats_path="/nonexistent/path.npy")

    def test_bad_stats_shape_raises(self):
        """stats 文件形状不是 (2, action_dim) 时应报错而非广播出诡异结果"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            self._create_dummy_episode(base, n_frames=10, action_dim=4)
            stats_path = base / "bad_stats.npy"
            np.save(stats_path, np.zeros((2, 3), dtype=np.float32))
            with self.assertRaisesRegex(ValueError, "形状"):
                ActionDataset(tmpdir, history_len=3, action_dim=4,
                              image_width=32, image_height=32,
                              frame_len=3, normalize_actions=True,
                              action_stats_path=str(stats_path))

    def test_action_dim_mismatch_raises(self):
        """actions.npy 列数与 action_dim 不一致应在加载时抛 ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ep_dir = base / "episode_0001"
            images_dir = ep_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            for i in range(8):
                Image.fromarray(np.random.randint(0, 255, (16, 16, 3), dtype=np.uint8)
                                ).save(images_dir / f"{i:04d}.jpg")
            np.save(ep_dir / "actions.npy", np.random.randn(8, 6).astype(np.float32))
            with self.assertRaisesRegex(ValueError, "actions.npy"):
                ActionDataset(tmpdir, history_len=3, action_dim=4)

    def test_frame_mismatch_warns(self):
        """图像与动作帧数不一致时应打印 WARNING 并截断"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ep_dir = base / "episode_0001"
            images_dir = ep_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            for i in range(8):
                Image.fromarray(np.random.randint(0, 255, (16, 16, 3), dtype=np.uint8)
                                ).save(images_dir / f"{i:04d}.jpg")
            np.save(ep_dir / "actions.npy", np.random.randn(12, 4).astype(np.float32))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ds = ActionDataset(tmpdir, history_len=3, action_dim=4)
            self.assertIn("WARNING", buf.getvalue())
            self.assertEqual(len(ds), 8 - 3)  # 按较少者（8 帧图像）截断

    def test_stats_cover_history_values(self):
        """归一化统计量必须覆盖 history 独有取值，否则 z-score 爆炸

        col0 仅在 history（首行）出现 100，targets 全为 0：只按 targets
        算统计时 std 落到 1e-8 地板值，z=(100-0)/1e-8=1e10。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ep_dir = base / "episode_0001"
            images_dir = ep_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            for i in range(7):
                Image.fromarray(np.random.randint(0, 255, (16, 16, 3), dtype=np.uint8)
                                ).save(images_dir / f"{i:04d}.jpg")
            actions = np.zeros((7, 4), dtype=np.float32)
            actions[0, 0] = 100.0
            np.save(ep_dir / "actions.npy", actions)
            ds = ActionDataset(tmpdir, history_len=5, action_dim=4,
                               normalize_actions=True)
            self.assertGreater(ds._action_std[0], 1.0)  # 而非 1e-8 地板值
            z_max = (np.abs(actions[:, 0] - ds._action_mean[0])
                     / ds._action_std[0]).max()
            self.assertLess(z_max, 10.0)


class TestConfig(unittest.TestCase):
    """测试配置和 CLI 参数"""

    def test_default_config(self):
        cfg = Config()
        self.assertEqual(cfg.data.image_width, 640)
        self.assertEqual(cfg.data.image_height, 480)
        self.assertEqual(cfg.data.action_dim, 18)
        self.assertEqual(cfg.data.history_len, 5)
        self.assertEqual(cfg.data.frame_len, 3)
        self.assertEqual(cfg.model.cnn_backbone, "simple")
        self.assertEqual(cfg.model.attn_dim, 128)
        self.assertEqual(cfg.model.attn_heads, 4)
        self.assertEqual(cfg.model.attn_layers, 2)
        self.assertEqual(cfg.train.epochs, 100)

    def test_apply_overrides(self):
        parser = build_argparser("test")
        args = parser.parse_args(["--epochs", "50", "--lr", "0.01",
                                  "--backbone", "mobilenet",
                                  "--frame-len", "4", "--attn-dim", "64"])
        cfg = apply_overrides(Config(), args)
        self.assertEqual(cfg.train.epochs, 50)
        self.assertEqual(cfg.train.lr, 0.01)
        self.assertEqual(cfg.model.cnn_backbone, "mobilenet")
        self.assertEqual(cfg.data.frame_len, 4)
        self.assertEqual(cfg.model.attn_dim, 64)

    def test_normalize_actions_flag(self):
        """--normalize-actions 和 --no-normalize-actions 都应可用"""
        parser = build_argparser("test")
        args = parser.parse_args(["--normalize-actions"])
        self.assertTrue(args.normalize_actions)

        parser = build_argparser("test")
        args = parser.parse_args(["--no-normalize-actions"])
        self.assertFalse(args.normalize_actions)

        parser = build_argparser("test")
        args = parser.parse_args([])
        self.assertIsNone(args.normalize_actions)


class TestMtParser(unittest.TestCase):
    """测试 .mt 脚本 @Action 解析器"""

    def test_parse_basic_and_forward_fill(self):
        """正常解析：跳过表头/注释，`-` 沿用上一行同列值"""
        content = (
            "@Action\n"
            "s1.A  s2.A  spk.F;\t# column name\n"
            "  10    20    100;\n"
            "  11    21    101;\n"
            "   -    22      -;\n"
            "@\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "basic.mt", content)
            matrix = parse_mt_action(path)
        self.assertEqual(matrix.shape, (3, 3))
        self.assertEqual(matrix.dtype, np.float32)
        np.testing.assert_array_equal(matrix[0], [10, 20, 100])
        np.testing.assert_array_equal(matrix[2], [11, 22, 101])

    def test_first_row_dash_fills_zero(self):
        """首行 `-` 填 0（与 pybot/bot4cj 运行时语义一致），后续行 `-` 沿用"""
        content = (
            "@Servo\n"
            " name   max  min;\n"
            "  s1    90     -;\n"
            "  s2   180     -;\n"
            "@\n"
            "\n"
            "@Action\n"
            "s1.A  s2.A  spk.F;\n"
            "   -     -      -;\n"
            "  45     -     10;\n"
            "@\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "servo.mt", content)
            matrix = parse_mt_action(path)
        np.testing.assert_array_equal(matrix[0], [0, 0, 0])
        np.testing.assert_array_equal(matrix[1], [45, 0, 10])

    def test_action_as_last_block_parses(self):
        """@Action 为文件最后一个块（无后续 @ 段）时也应解析成功。

        回归：旧正则 @Action\\s+(.*?)\\s*@ 要求后续必有 @，
        末位块会报"未找到 @Action 块"，parse_mt_directory 进而
        静默跳过该文件，造成预训练数据丢失。"""
        content = (
            "@Servo\n"
            " name   max  min;\n"
            "  s1    90     -;\n"
            "@\n"
            "\n"
            "@Action\n"
            "s1.A  s2.A  spk.F;\n"
            "  10    20    100;\n"
            "   -    22      -;\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "lastblock.mt", content)
            matrix = parse_mt_action(path)
        np.testing.assert_array_equal(matrix, [[10, 20, 100], [10, 22, 100]])

    def test_std_mt_first_row_matches_pybot(self):
        """跨模块回归锚点：std.mt 首行解析结果与 pybot 运行时执行值一致

        与 pybot/tests/test_hardvars.py 的 journal 期望值互为对照：
        首行 `-`（s2/s4/s6/s8）解析为 0，而非 @Servo max 列。
        """
        content = (
            "@Servo\n"
            " name   max  min  offset  controller  channel;\n"
            "  s1    90     -       -   pca9685-0        0;\n"
            "  s2   180     -       -           -        1;\n"
            "  s3   -90     -       -           -        2;\n"
            "  s4  -180     -       -           -        3;\n"
            "@\n"
            "\n"
            "@Action\n"
            "s1.A  s2.A  s3.A  s4.A  s5.A  s6.A  s7.A  s8.A  s9.A  s10.A  spk.F  spk.delay;\n"
            "  45     -    45     -    45     -    45     -    90     90     10       200;\n"
            "@\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "std.mt", content)
            matrix = parse_mt_action(path)
        np.testing.assert_array_equal(
            matrix[0], [45, 0, 45, 0, 45, 0, 45, 0, 90, 90, 10, 200])

    def test_no_action_block_raises(self):
        """无 @Action 块应抛 ValueError"""
        content = "@Servo\nname  max;\n  s1   90;\n@\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "noaction.mt", content)
            with self.assertRaisesRegex(ValueError, "@Action"):
                parse_mt_action(path)

    def test_header_only_raises(self):
        """只有表头没有数据行应抛 ValueError"""
        content = "@Action\na.A  b.A;\n@\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "empty.mt", content)
            with self.assertRaises(ValueError):
                parse_mt_action(path)

    def test_row_col_mismatch_raises(self):
        """数据行列数与表头不一致应抛带行号的 ValueError"""
        content = "@Action\na.A  b.A;\n  1    2;\n  1    2    3;\n@\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "mismatch.mt", content)
            with self.assertRaisesRegex(ValueError, "第 3 行"):
                parse_mt_action(path)

    def test_invalid_value_raises(self):
        """非数值且非 `-` 的单元格应抛带行列号的 ValueError"""
        content = "@Action\na.A  b.A;\n  1  xyz;\n@\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_mt(tmpdir, "badval.mt", content)
            with self.assertRaisesRegex(ValueError, "xyz"):
                parse_mt_action(path)

    def test_directory_skips_bad_files_with_warning(self):
        """parse_mt_directory 跳过坏文件并打印 WARNING，保留好文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_mt(tmpdir, "good.mt", _make_seq_mt(4))
            _write_mt(tmpdir, "bad.mt", "@Action\na.A  b.A;\n  1  xyz;\n@\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                results = parse_mt_directory(tmpdir)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][0].endswith("good.mt"))
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("bad.mt", buf.getvalue())


class TestMTActionDataset(unittest.TestCase):
    """测试预训练滑动窗口数据集"""

    @staticmethod
    def _build(mt_dir, history_len, action_dim, file_indices=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return MTActionDataset(mt_dir, history_len, action_dim,
                                   file_indices=file_indices)

    def test_sliding_window_count_and_shapes(self):
        """N 行数据、窗口 H → N-H 个样本，形状与 target 值正确"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_mt(tmpdir, "a.mt", _make_seq_mt(7))
            ds = self._build(tmpdir, history_len=3, action_dim=3)
            self.assertEqual(len(ds), 4)  # 7 - 3 = 4 samples
            sample = ds[0]
            self.assertEqual(sample["action_history"].shape, (3, 3))
            self.assertEqual(sample["target"].shape, (3,))
            # 样本 0 的 target 是第 4 行数据 [3, 30, 300]
            np.testing.assert_array_equal(sample["target"].numpy(), [3, 30, 300])

    def test_pad_columns_to_action_dim(self):
        """列数 < action_dim 时右侧补零"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_mt(tmpdir, "a.mt", _make_seq_mt(5))
            ds = self._build(tmpdir, history_len=3, action_dim=5)
            sample = ds[0]
            self.assertEqual(sample["action_history"].shape, (3, 5))
            np.testing.assert_array_equal(
                sample["action_history"].numpy()[:, 3:], np.zeros((3, 2)))
            np.testing.assert_array_equal(sample["target"].numpy()[3:], [0, 0])

    def test_truncate_columns_to_action_dim(self):
        """列数 > action_dim 时截断多余列"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_mt(tmpdir, "a.mt", _make_seq_mt(5))
            ds = self._build(tmpdir, history_len=3, action_dim=2)
            sample = ds[0]
            self.assertEqual(sample["action_history"].shape, (3, 2))
            np.testing.assert_array_equal(sample["target"].numpy(), [3, 30])

    def test_file_indices_filter(self):
        """file_indices 只加载指定文件，实现文件级 train/val 划分"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_mt(tmpdir, "a.mt", _make_seq_mt(7))  # 4 samples
            _write_mt(tmpdir, "b.mt", _make_seq_mt(4))  # 1 sample
            ds_all = self._build(tmpdir, history_len=3, action_dim=3)
            ds_a = self._build(tmpdir, history_len=3, action_dim=3,
                               file_indices=[0])
            ds_b = self._build(tmpdir, history_len=3, action_dim=3,
                               file_indices=[1])
            self.assertEqual(len(ds_all), 5)
            self.assertEqual(len(ds_a), 4)
            self.assertEqual(len(ds_b), 1)


class TestValidateConfigValues(unittest.TestCase):
    """测试 checkpoint config 值域校验"""

    def test_default_config_passes(self):
        cfg = Config()
        validate_config_values(cfg.data, cfg.model)  # 不应抛异常

    def test_out_of_range_or_non_int_raises(self):
        for field, value in [("image_width", 99999), ("image_width", 0),
                             ("image_height", 0),
                             ("history_len", -1), ("frame_len", 0),
                             ("frame_len", 99999), ("action_dim", "18")]:
            cfg = Config()
            setattr(cfg.data, field, value)
            with self.assertRaises(ValueError, msg=f"{field}={value}"):
                validate_config_values(cfg.data, cfg.model)

    def test_model_field_out_of_range_raises(self):
        """model 字段越界（fused_hidden_dim 超限、dropout 越界、attn_* 越界）应抛 ValueError"""
        for field, value in [("fused_hidden_dim", 10 ** 9),
                             ("fused_hidden_dim", 0),
                             ("cnn_feature_dim", 99999),
                             ("attn_dim", 0),
                             ("attn_dim", 99999),
                             ("attn_heads", 0),
                             ("attn_heads", 128),
                             ("attn_layers", 0),
                             ("dropout", 1.5),
                             ("dropout", -0.1)]:
            cfg = Config()
            setattr(cfg.model, field, value)
            with self.assertRaises(ValueError, msg=f"{field}={value}"):
                validate_config_values(cfg.data, cfg.model)

    def test_attn_dim_not_divisible_by_heads_raises(self):
        """attn_dim 不能被 attn_heads 整除应抛 ValueError"""
        cfg = Config()
        cfg.model.attn_dim = 130
        cfg.model.attn_heads = 4
        with self.assertRaisesRegex(ValueError, "整除"):
            validate_config_values(cfg.data, cfg.model)

    def test_train_field_out_of_range_raises(self):
        """train 字段越界（lr 为负、val_split>1 等）应抛 ValueError"""
        for field, value in [("lr", -1.0), ("lr", 0.0),
                             ("val_split", 2.0), ("val_split", -0.1),
                             ("batch_size", 0), ("epochs", 0),
                             ("patience", -1), ("num_workers", -1),
                             ("max_grad_norm", -1.0), ("weight_decay", -1.0)]:
            cfg = Config()
            setattr(cfg.train, field, value)
            with self.assertRaises(ValueError, msg=f"{field}={value}"):
                validate_config_values(cfg.data, cfg.model, cfg.train)

    def test_default_train_passes(self):
        cfg = Config()
        validate_config_values(cfg.data, cfg.model, cfg.train)  # 不应抛异常


class TestParseCheckpointConfig(unittest.TestCase):
    """测试 checkpoint config 结构解析（未知键/缺段应给明确错误而非裸 TypeError）"""

    def test_valid_config_roundtrip(self):
        cfg = Config()
        parsed = parse_checkpoint_config(cfg.to_dict())
        self.assertEqual(parsed.data.action_dim, cfg.data.action_dim)
        self.assertEqual(parsed.model.cnn_backbone, cfg.model.cnn_backbone)
        self.assertEqual(parsed.train.epochs, cfg.train.epochs)

    def test_unknown_key_raises(self):
        cfg = Config()
        d = cfg.to_dict()
        d["data"]["evil_extra_key"] = 123
        with self.assertRaisesRegex(ValueError, "evil_extra_key"):
            parse_checkpoint_config(d)

    def test_legacy_checkpoint_config_rejected(self):
        """旧架构 checkpoint（含 history_encoder/action_hidden_dim）应拒载"""
        cfg = Config()
        d = cfg.to_dict()
        d["model"]["history_encoder"] = "mlp"
        d["model"]["action_hidden_dim"] = 64
        with self.assertRaisesRegex(ValueError, "history_encoder"):
            parse_checkpoint_config(d)

    def test_missing_section_raises(self):
        cfg = Config()
        d = cfg.to_dict()
        del d["data"]
        with self.assertRaisesRegex(ValueError, "data"):
            parse_checkpoint_config(d)

    def test_out_of_range_raises(self):
        cfg = Config()
        d = cfg.to_dict()
        d["model"]["fused_hidden_dim"] = 10 ** 9
        with self.assertRaisesRegex(ValueError, "fused_hidden_dim"):
            parse_checkpoint_config(d)

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            parse_checkpoint_config("not a dict")

    def test_train_section_unknown_key_raises(self):
        """train 段含未知键也应报 ValueError（与 data/model 段一致）"""
        cfg = Config()
        d = cfg.to_dict()
        d["train"]["unknown_field"] = 1
        with self.assertRaisesRegex(ValueError, "train.*unknown_field"):
            parse_checkpoint_config(d)

    def test_train_section_non_dict_raises(self):
        """train 段为非 dict 应报 ValueError，而非裸 TypeError"""
        cfg = Config()
        d = cfg.to_dict()
        d["train"] = "not a dict"
        with self.assertRaisesRegex(ValueError, "train"):
            parse_checkpoint_config(d)

    def test_missing_train_section_still_ok(self):
        """旧 checkpoint 无 train 段（推理 checkpoint 可能被裁剪）应可用默认值"""
        cfg = Config()
        d = cfg.to_dict()
        del d["train"]
        parsed = parse_checkpoint_config(d)
        self.assertEqual(parsed.train.batch_size, TrainConfig().batch_size)

    def test_warn_init_from_mismatch(self):
        """--init-from 兼容性警告：normalize 或 action_dim 不一致时提示"""
        from config import warn_init_from_mismatch
        cfg = Config()
        init_cfg = {
            "data": {"normalize_actions": True, "action_dim": 12},
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            warn_init_from_mismatch(init_cfg, cfg)
        out = buf.getvalue()
        self.assertIn("normalize_actions=True", out)
        self.assertIn("action_dim=12", out)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            warn_init_from_mismatch(
                {"data": {"normalize_actions": False,
                          "action_dim": cfg.data.action_dim}}, cfg)
        self.assertEqual(buf.getvalue(), "")
        with contextlib.redirect_stdout(buf):
            warn_init_from_mismatch(None, cfg)
        self.assertEqual(buf.getvalue(), "")


class TestDataRecorderValidation(unittest.TestCase):
    """测试 DataRecorder 的输入校验与目录原子分配"""

    def test_wrong_action_dim_raises(self):
        """record 传入错误维度的 action 应抛 ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = DataRecorder(tmpdir, action_dim=4)
            img = np.zeros((32, 32, 3), dtype=np.uint8)
            with self.assertRaisesRegex(ValueError, "维度"):
                recorder.record(img, np.zeros(3, dtype=np.float32))

    def test_dir_collision_retries_next_index(self):
        """目标 episode 目录已被占用时应重试递增索引（模拟并发竞态）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            r1 = DataRecorder(tmpdir, action_dim=4)
            self.assertIn("episode_0000", str(r1.episode_dir))
            # 让 glob 返回空 → r2 从 0 开始分配，与 r1 冲突后应重试到 0001
            with mock.patch.object(Path, "glob", return_value=iter([])):
                r2 = DataRecorder(tmpdir, action_dim=4)
            self.assertIn("episode_0001", str(r2.episode_dir))


class TestActionPredictorInference(unittest.TestCase):
    """测试推理封装端到端：checkpoint 保存 → 加载 → 推理"""

    @staticmethod
    def _make_checkpoint(tmpdir, normalize_actions=False, image_width=32,
                         image_height=32, frame_len=3):
        cfg = Config()
        cfg.data.image_width = image_width
        cfg.data.image_height = image_height
        cfg.data.action_dim = 4
        cfg.data.history_len = 3
        cfg.data.frame_len = frame_len
        cfg.data.normalize_actions = normalize_actions
        model = ActionPredictor(cfg.model, cfg.data)
        model.eval()
        path = os.path.join(tmpdir, "best_inference.pt")
        torch.save({"model_state_dict": model.state_dict(),
                    "config": cfg.to_dict()}, path)
        return path, model, cfg

    def test_end_to_end_predict(self):
        """加载 checkpoint 后多帧推理输出形状正确、数值有限且可复现"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _, _ = self._make_checkpoint(tmpdir)
            predictor = ActionPredictorInference(path, device="cpu")
            self.assertEqual(predictor.action_dim, 4)
            self.assertEqual(predictor.history_len, 3)
            self.assertEqual(predictor.frame_len, 3)

            images = [np.random.randint(0, 255, (32, 32, 3), dtype=np.uint8)
                      for _ in range(3)]
            history = np.random.randn(3, 4).astype(np.float32)
            pred1 = predictor.predict(images, history)
            pred2 = predictor.predict(images, history)
            self.assertEqual(pred1.shape, (4,))
            self.assertTrue(np.all(np.isfinite(pred1)))
            np.testing.assert_array_equal(pred1, pred2)  # eval 模式确定性

    def test_loaded_weights_match_saved(self):
        """加载后的模型权重应与保存时逐张量一致"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, model, _ = self._make_checkpoint(tmpdir)
            predictor = ActionPredictorInference(path, device="cpu")
            loaded_sd = predictor._model.state_dict()
            for key, tensor in model.state_dict().items():
                self.assertTrue(torch.equal(loaded_sd[key].cpu(), tensor),
                                msg=f"权重不一致: {key}")

    def test_wrong_history_shape_raises(self):
        """action_history 形状错误应抛 ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _, _ = self._make_checkpoint(tmpdir)
            predictor = ActionPredictorInference(path, device="cpu")
            images = [np.zeros((32, 32, 3), dtype=np.uint8)] * 3
            with self.assertRaisesRegex(ValueError, "action_history"):
                predictor.predict(images, np.zeros((2, 4), dtype=np.float32))

    def test_wrong_frame_count_raises(self):
        """传入帧数与 frame_len 不一致应抛 ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _, _ = self._make_checkpoint(tmpdir)
            predictor = ActionPredictorInference(path, device="cpu")
            images = [np.zeros((32, 32, 3), dtype=np.uint8)] * 2
            with self.assertRaisesRegex(ValueError, "帧"):
                predictor.predict(images, np.zeros((3, 4), dtype=np.float32))

    def test_missing_action_stats_raises(self):
        """启用归一化但缺 action_stats.npy 应抛 FileNotFoundError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _, _ = self._make_checkpoint(tmpdir, normalize_actions=True)
            with self.assertRaises(FileNotFoundError):
                ActionPredictorInference(path, device="cpu")

    def test_normalize_identity_stats_matches_raw(self):
        """mean=0/std=1 的归一化路径输出应与非归一化一致"""
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = os.path.join(tmpdir, "raw")
            norm_dir = os.path.join(tmpdir, "norm")
            os.makedirs(raw_dir)
            os.makedirs(norm_dir)
            torch.manual_seed(0)
            raw_path, model, cfg = self._make_checkpoint(raw_dir)
            # 同一份权重另存为启用归一化的 checkpoint
            cfg.data.normalize_actions = True
            norm_path = os.path.join(norm_dir, "best_inference.pt")
            torch.save({"model_state_dict": model.state_dict(),
                        "config": cfg.to_dict()}, norm_path)
            stats = np.stack([np.zeros(4), np.ones(4)]).astype(np.float32)
            np.save(os.path.join(norm_dir, "action_stats.npy"), stats)

            image = np.random.randint(0, 255, (32, 32, 3), dtype=np.uint8)
            history = np.random.randn(3, 4).astype(np.float32)
            pred_raw = ActionPredictorInference(
                raw_path, device="cpu").predict([image] * 3, history)
            pred_norm = ActionPredictorInference(
                norm_path, device="cpu").predict([image] * 3, history)
            np.testing.assert_allclose(pred_norm, pred_raw, rtol=1e-5, atol=1e-6)

    def test_stats_shape_mismatch_raises(self):
        """action_stats.npy 形状与 action_dim 不一致应抛 ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _, _ = self._make_checkpoint(tmpdir, normalize_actions=True)
            np.save(os.path.join(tmpdir, "action_stats.npy"), np.zeros((2, 7)))
            with self.assertRaisesRegex(ValueError, "action_stats.npy"):
                ActionPredictorInference(path, device="cpu")

    def test_invalid_config_in_checkpoint_raises(self):
        """checkpoint 中 config 值域非法应在加载时抛 ValueError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, model, cfg = self._make_checkpoint(tmpdir)
            cfg_dict = cfg.to_dict()
            cfg_dict["data"]["image_width"] = 99999
            bad_path = os.path.join(tmpdir, "bad.pt")
            torch.save({"model_state_dict": model.state_dict(),
                        "config": cfg_dict}, bad_path)
            with self.assertRaisesRegex(ValueError, "image_width"):
                ActionPredictorInference(bad_path, device="cpu")

    def test_unknown_config_key_in_checkpoint_raises(self):
        """checkpoint config 含未知键应在加载时抛带键名的 ValueError 而非裸 TypeError"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, model, cfg = self._make_checkpoint(tmpdir)
            cfg_dict = cfg.to_dict()
            cfg_dict["data"]["evil_extra_key"] = 123
            bad_path = os.path.join(tmpdir, "bad.pt")
            torch.save({"model_state_dict": model.state_dict(),
                        "config": cfg_dict}, bad_path)
            with self.assertRaisesRegex(ValueError, "evil_extra_key"):
                ActionPredictorInference(bad_path, device="cpu")


@unittest.skipUnless(importlib.util.find_spec("onnxscript") is not None,
                     "torch.onnx.export 需要 onnxscript（当前环境未安装）")
class TestExportOnnx(unittest.TestCase):
    """测试 ONNX 导出（环境缺 onnxscript 时自动跳过）"""

    def test_export_creates_onnx_file(self):
        """从 checkpoint 导出 ONNX 文件应成功生成"""
        from export_onnx import main as export_onnx_main
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _, _ = TestActionPredictorInference._make_checkpoint(tmpdir)
            out_path = os.path.join(tmpdir, "model.onnx")
            argv = ["export_onnx.py", "--model", path, "--output", out_path]
            with mock.patch("sys.argv", argv):
                export_onnx_main()
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 0)


class TestDeployWrapper(unittest.TestCase):
    """部署版导出（export_deploy.DeployWrapper）的图等价性"""

    def test_pool_kernel_matches_conv_output(self):
        """固定 kernel AvgPool 必须覆盖卷积真实输出尺寸。

        回归：旧实现用 H//16 取 kernel，1080 高度下卷积输出 68 行、
        kernel 取 67 会静默丢弃末行（训练/部署图数值不等价）。"""
        from export_deploy import DeployWrapper
        cfg = Config()
        cfg.data.image_height, cfg.data.image_width = 1080, 1920
        model = ActionPredictor(cfg.model, cfg.data, pretrained=False).eval()
        w = DeployWrapper(model)
        self.assertEqual(w.pool.kernel_size, (68, 120))
        cfg.data.image_height, cfg.data.image_width = 224, 224
        model = ActionPredictor(cfg.model, cfg.data, pretrained=False).eval()
        w = DeployWrapper(model)
        self.assertEqual(w.pool.kernel_size, (14, 14))

    def test_mobilenet_backbone_rejected(self):
        """MobileNet 的 DFC 改造未实现，应给出明确错误而非 AttributeError"""
        from export_deploy import DeployWrapper
        cfg = Config()
        cfg.model.cnn_backbone = "mobilenet"
        model = ActionPredictor(cfg.model, cfg.data, pretrained=False).eval()
        with self.assertRaises(ValueError):
            DeployWrapper(model)


class TestCompileAndCalibrationHelpers(unittest.TestCase):
    """dfc_compile / gen_calibration 的辅助函数（P3 修复回归）"""

    def test_start_nodes_follows_frame_count(self):
        """start_nodes 按 frame_count 生成输入名（旧实现硬编码 3 帧）"""
        from dfc_compile import start_nodes
        self.assertEqual(start_nodes(3), [
            "frame0", "frame1", "frame2", "action_history", "pred_token"])
        self.assertEqual(start_nodes(1),
                         ["frame0", "action_history", "pred_token"])

    def test_build_image_samples_memmap_chunked(self):
        """分块 memmap 生成的校准图像形状/dtype/值域正确，且可清理"""
        from gen_calibration import build_image_samples
        rng = np.random.default_rng(0)
        images = build_image_samples(
            n_samples=6, frame_len=2, height=16, width=16, rng=rng,
            chunk_samples=2)
        try:
            self.assertIsInstance(images, np.memmap)
            self.assertEqual(images.shape, (6, 2, 3, 16, 16))
            self.assertEqual(images.dtype, np.uint8)
            self.assertTrue((images <= 255).all())
            self.assertTrue(os.path.exists(images.filename))
        finally:
            images._mmap.close()
            os.unlink(images.filename)

    def test_build_image_samples_invalid_chunk(self):
        from gen_calibration import build_image_samples
        with self.assertRaises(ValueError):
            build_image_samples(4, 2, 16, 16, np.random.default_rng(0),
                                chunk_samples=0)


class TestResumeCheckpoint(unittest.TestCase):
    """测试 --resume 断点续训练：checkpoint 格式与状态恢复"""

    def test_last_checkpoint_contains_resume_keys(self):
        """last.pt 必须包含恢复训练所需的全部键"""
        required_keys = {
            "epoch", "model_state_dict", "optimizer_state_dict",
            "scheduler_state_dict", "best_val_loss", "patience_counter",
            "config",
        }
        cfg = Config()
        cfg.data.action_dim = 4
        model = ActionPredictor(cfg.model, cfg.data)
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=100)
        with tempfile.TemporaryDirectory() as tmpdir:
            last_path = os.path.join(tmpdir, "last.pt")
            torch.save({
                "epoch": 10,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "scheduler_state_dict": scheduler.state_dict(),
                "best_val_loss": 0.5,
                "patience_counter": 3,
                "config": cfg.to_dict(),
            }, last_path)
            ckpt = torch.load(last_path, map_location="cpu", weights_only=True)
            self.assertTrue(required_keys.issubset(ckpt.keys()),
                            f"缺少键: {required_keys - set(ckpt.keys())}")
            self.assertEqual(ckpt["epoch"], 10)
            self.assertEqual(ckpt["best_val_loss"], 0.5)
            self.assertEqual(ckpt["patience_counter"], 3)

    def test_resume_restores_model_weights(self):
        """resume 加载后模型权重应与保存时一致"""
        cfg = Config()
        cfg.data.action_dim = 4
        torch.manual_seed(0)
        model = ActionPredictor(cfg.model, cfg.data)
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=100)
        with tempfile.TemporaryDirectory() as tmpdir:
            last_path = os.path.join(tmpdir, "last.pt")
            torch.save({
                "epoch": 5,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "scheduler_state_dict": scheduler.state_dict(),
                "best_val_loss": 1.0,
                "patience_counter": 0,
                "config": cfg.to_dict(),
            }, last_path)
            # 重新创建模型并恢复
            model2 = ActionPredictor(cfg.model, cfg.data)
            ckpt = torch.load(last_path, map_location="cpu", weights_only=True)
            model2.load_state_dict(ckpt["model_state_dict"])
            for k, v in model.state_dict().items():
                self.assertTrue(torch.equal(model2.state_dict()[k], v),
                                msg=f"权重不一致: {k}")




@unittest.skipUnless(
    importlib.util.find_spec("onnxscript") is not None
    and importlib.util.find_spec("onnxruntime") is not None,
    "OnnxPredictor 测试需要 onnxscript + onnxruntime")
class TestOnnxPredictor(unittest.TestCase):
    """ONNX Runtime 后端测试：导出小模型后与 torch 前向对齐"""

    @staticmethod
    def _export_small_onnx(tmpdir):
        cfg = Config()
        cfg.data.image_width = 32
        cfg.data.image_height = 32
        cfg.data.action_dim = 4
        cfg.data.history_len = 3
        cfg.data.frame_len = 3
        model = ActionPredictor(cfg.model, cfg.data)
        model.eval()
        out_path = os.path.join(tmpdir, "model.onnx")
        torch.onnx.export(
            model,
            (torch.randn(1, 3, 3, 32, 32), torch.randn(1, 3, 4)),
            out_path,
            input_names=["images", "action_history"],
            output_names=["predicted_action"],
            opset_version=18)
        return out_path, model

    def _frames(self, n):
        return [np.random.randint(0, 255, (48, 64, 3), dtype=np.uint8)
                for _ in range(n)]

    def test_dims_derived_from_session(self):
        from onnx_infer import OnnxPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = self._export_small_onnx(tmpdir)
            with OnnxPredictor(path) as p:
                self.assertEqual((p.frame_len, p.history_len, p.action_dim),
                                 (3, 3, 4))
                self.assertEqual((p.image_width, p.image_height), (32, 32))

    def test_matches_torch_forward(self):
        from onnx_infer import OnnxPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            path, model = self._export_small_onnx(tmpdir)
            frames = self._frames(3)
            history = np.random.randn(3, 4).astype(np.float32)
            with OnnxPredictor(path) as p:
                onnx_out = p.predict(frames, history)
                pre = np.stack([p._preprocess(f) for f in frames])
                with torch.no_grad():
                    torch_out = model(
                        torch.from_numpy(pre[np.newaxis]),
                        torch.from_numpy(history[np.newaxis])).numpy()
            self.assertEqual(onnx_out.shape, (4,))
            np.testing.assert_allclose(onnx_out, torch_out.reshape(-1),
                                       atol=1e-4)

    def test_frame_count_mismatch_raises(self):
        from onnx_infer import OnnxPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = self._export_small_onnx(tmpdir)
            with OnnxPredictor(path) as p:
                with self.assertRaises(ValueError):
                    p.predict(self._frames(1), np.zeros((3, 4), np.float32))

    def test_history_shape_mismatch_raises(self):
        from onnx_infer import OnnxPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = self._export_small_onnx(tmpdir)
            with OnnxPredictor(path) as p:
                with self.assertRaises(ValueError):
                    p.predict(self._frames(3), np.zeros((2, 4), np.float32))

    def test_stats_normalization_roundtrip(self):
        from onnx_infer import OnnxPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = self._export_small_onnx(tmpdir)
            stats_path = os.path.join(tmpdir, "action_stats.npy")
            mean, std = np.full(4, 0.5), np.full(4, 2.0)
            np.save(stats_path, np.stack([mean, std]))
            frames = self._frames(3)
            history = np.random.randn(3, 4).astype(np.float32)
            with OnnxPredictor(path) as plain, \
                    OnnxPredictor(path, stats_path=stats_path) as normed:
                expected = plain.predict(frames, (history - mean) / std)
                result = normed.predict(frames, history)
            np.testing.assert_allclose(result, expected * std + mean,
                                       atol=1e-5)

    def test_bad_stats_shape_raises(self):
        from onnx_infer import OnnxPredictor
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = self._export_small_onnx(tmpdir)
            stats_path = os.path.join(tmpdir, "action_stats.npy")
            np.save(stats_path, np.zeros((2, 5)))
            with self.assertRaises(ValueError):
                OnnxPredictor(path, stats_path=stats_path)


# ---------------- HailoPredictor 测试用 fake hailo_platform ----------------

class _FakeFormatType:
    UINT8 = "UINT8"
    FLOAT32 = "FLOAT32"


class _FakeFormat:
    def __init__(self, fmt_type):
        self.type = fmt_type


class _FakeQuantInfo:
    def __init__(self, qp_zp=0.0, qp_scale=1.0):
        self.qp_zp = qp_zp
        self.qp_scale = qp_scale


class _FakeVStreamInfo:
    def __init__(self, name, shape, fmt_type, quant=None):
        self.name = name
        self.shape = tuple(shape)
        self.format = _FakeFormat(fmt_type)
        self.quant_info = quant or _FakeQuantInfo()


class _FakeStream:
    def __init__(self):
        self._buffer = None

    def set_buffer(self, buf):
        self._buffer = buf

    def get_buffer(self):
        return self._buffer


class _FakeBindings:
    def __init__(self):
        self._streams = {}

    def input(self, name=""):
        return self._streams.setdefault(("in", name), _FakeStream())

    def output(self, name=""):
        return self._streams.setdefault(("out", name), _FakeStream())


class _FakeConfigured:
    def __init__(self, transform):
        self._transform = transform

    def create_bindings(self):
        return _FakeBindings()

    def run(self, bindings_list, timeout=0):
        for b in bindings_list:
            self._transform(b)


class _FakeInferModel:
    def __init__(self, transform):
        self._configured = _FakeConfigured(transform)

    @contextlib.contextmanager
    def configure(self):
        yield self._configured


class _FakeVDevice:
    def __init__(self, transform):
        self._transform = transform

    def create_infer_model(self, path):
        return _FakeInferModel(self._transform)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_hailo(test, infos_in, infos_out, transform):
    """向 sys.modules 注入 fake hailo_platform，无需硬件即可测试"""
    fake = types.ModuleType("hailo_platform")
    fake.FormatType = _FakeFormatType
    fake.HEF = lambda path: types.SimpleNamespace(
        get_input_vstream_infos=lambda: list(infos_in),
        get_output_vstream_infos=lambda: list(infos_out))
    fake.VDevice = lambda: _FakeVDevice(transform)
    patcher = mock.patch.dict(sys.modules, {"hailo_platform": fake})
    test.addCleanup(patcher.stop)
    patcher.start()


def _first_row_transform(hist_name, out_name, quant_in=None, quant_out=None):
    """transform：输出 = 历史动作首行（量化边缘时先反量化再量化）"""
    def transform(b):
        hist = np.asarray(b.input(hist_name).get_buffer(), dtype=np.float32)
        if quant_in is not None:
            hist = (hist - quant_in.qp_zp) * quant_in.qp_scale
        first = hist.reshape(-1, hist.shape[-1])[0]
        if quant_out is not None:
            first = np.clip(np.round(first / quant_out.qp_scale + quant_out.qp_zp),
                            0, 255).astype(np.uint8)
        b.output(out_name).get_buffer()[...] = first
    return transform


class TestHailoPredictor(unittest.TestCase):
    """HEF 推理封装测试（fake hailo_platform 注入，无需 Hailo 硬件）"""

    def _frames(self, n, size=8):
        return [np.random.randint(0, 255, (size, size, 3), dtype=np.uint8)
                for _ in range(n)]

    def test_float_ncfhw_layout(self):
        from hailo_infer import HailoPredictor
        infos_in = [
            _FakeVStreamInfo("net/images", (1, 2, 3, 4, 4), _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/history", (1, 2, 3), _FakeFormatType.FLOAT32),
        ]
        infos_out = [_FakeVStreamInfo("net/action", (1, 3), _FakeFormatType.FLOAT32)]
        seen_shapes = {}

        def transform(b):
            seen_shapes["image"] = np.asarray(
                b.input("net/images").get_buffer()).shape
            _first_row_transform("net/history", "net/action")(b)

        _install_fake_hailo(self, infos_in, infos_out, transform)
        history = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.float32)
        with HailoPredictor("fake.hef") as p:
            self.assertEqual((p.frame_len, p.history_len, p.action_dim), (2, 2, 3))
            self.assertEqual((p.image_width, p.image_height), (4, 4))
            result = p.predict(self._frames(2), history)
        np.testing.assert_allclose(result, [1, 2, 3])
        self.assertEqual(seen_shapes["image"], (1, 2, 3, 4, 4))

    def test_uint8_quantized_roundtrip(self):
        from hailo_infer import HailoPredictor
        q_in = _FakeQuantInfo(qp_zp=128.0, qp_scale=0.01)
        q_out = _FakeQuantInfo(qp_zp=10.0, qp_scale=0.5)
        infos_in = [
            _FakeVStreamInfo("net/images", (1, 2, 3, 4, 4), _FakeFormatType.UINT8),
            _FakeVStreamInfo("net/history", (1, 2, 3), _FakeFormatType.UINT8, q_in),
        ]
        infos_out = [_FakeVStreamInfo("net/action", (1, 3), _FakeFormatType.UINT8, q_out)]
        _install_fake_hailo(
            self, infos_in, infos_out,
            _first_row_transform("net/history", "net/action", q_in, q_out))
        history = np.array([[0.5, -0.25, 0.0], [1, 1, 1]], dtype=np.float32)
        with HailoPredictor("fake.hef") as p:
            result = p.predict(self._frames(2), history)
        np.testing.assert_allclose(result, [0.5, -0.25, 0.0], atol=0.51)

    def test_nhwc_fused_layout(self):
        from hailo_infer import HailoPredictor
        infos_in = [
            _FakeVStreamInfo("net/images", (1, 4, 4, 6), _FakeFormatType.UINT8),
            _FakeVStreamInfo("net/history", (1, 2, 3), _FakeFormatType.FLOAT32),
        ]
        infos_out = [_FakeVStreamInfo("net/action", (1, 3), _FakeFormatType.FLOAT32)]
        seen_shapes = {}

        def transform(b):
            seen_shapes["image"] = np.asarray(
                b.input("net/images").get_buffer()).shape
            _first_row_transform("net/history", "net/action")(b)

        _install_fake_hailo(self, infos_in, infos_out, transform)
        history = np.zeros((2, 3), dtype=np.float32)
        with HailoPredictor("fake.hef") as p:
            self.assertEqual(p.frame_len, 2)      # 6 通道 = 2 帧 × 3
            result = p.predict(self._frames(2), history)
        np.testing.assert_allclose(result, [0, 0, 0])
        self.assertEqual(seen_shapes["image"], (1, 4, 4, 6))

    def test_multiframe_inputs_layout(self):
        """新版 5 输入 HEF：frame×3 (H,W,3) + history + pred_token 常量"""
        from hailo_infer import HailoPredictor
        q_frame = _FakeQuantInfo(qp_zp=114.0, qp_scale=0.018723)
        q_pred = _FakeQuantInfo(qp_zp=123.0, qp_scale=18571.0)
        infos_in = [
            _FakeVStreamInfo("net/input_layer1", (64, 64, 3),
                             _FakeFormatType.UINT8, q_frame),
            _FakeVStreamInfo("net/input_layer2", (64, 64, 3),
                             _FakeFormatType.UINT8, q_frame),
            _FakeVStreamInfo("net/input_layer3", (64, 64, 3),
                             _FakeFormatType.UINT8, q_frame),
            _FakeVStreamInfo("net/input_layer4", (1, 5, 18),
                             _FakeFormatType.UINT8,
                             _FakeQuantInfo(qp_zp=0.0, qp_scale=1.960784)),
            _FakeVStreamInfo("net/input_layer5", (1, 1, 128),
                             _FakeFormatType.UINT8, q_pred),
        ]
        infos_out = [_FakeVStreamInfo(
            "net/conv49", (1, 1, 18), _FakeFormatType.UINT8,
            _FakeQuantInfo(qp_zp=2.0, qp_scale=0.837115))]
        seen = {}

        def transform(b):
            for name in ("net/input_layer1", "net/input_layer2", "net/input_layer3"):
                seen[name] = np.asarray(b.input(name).get_buffer())
            seen["history"] = np.asarray(b.input("net/input_layer4").get_buffer())
            seen["pred"] = np.asarray(b.input("net/input_layer5").get_buffer())
            b.output("net/conv49").get_buffer()[...] = 2   # 反量化后为 0

        _install_fake_hailo(self, infos_in, infos_out, transform)
        with HailoPredictor("fake.hef") as p:
            self.assertEqual((p.frame_len, p.history_len, p.action_dim), (3, 5, 18))
            self.assertEqual((p.image_width, p.image_height), (64, 64))
            result = p.predict(self._frames(3, size=64), np.zeros((5, 18), np.float32))
        self.assertEqual(seen["net/input_layer1"].dtype, np.uint8)
        self.assertEqual(seen["net/input_layer1"].shape, (64, 64, 3))
        self.assertEqual(seen["history"].shape, (1, 5, 18))
        np.testing.assert_array_equal(seen["history"], 0)
        np.testing.assert_array_equal(seen["pred"], 123)   # 退化量化：喂 0 → q=123
        np.testing.assert_allclose(result, 0)

    def test_multiframe_scrambled_report_order(self):
        """HEF vstream 报告顺序可能被打乱（实测 1,5,4,3,2），
        帧输入必须按 input_layerN 排序绑定，否则 frame1/frame2 错位"""
        from hailo_infer import HailoPredictor
        infos_in = [
            _FakeVStreamInfo("net/input_layer1", (8, 8, 3),
                             _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/input_layer3", (8, 8, 3),
                             _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/input_layer2", (8, 8, 3),
                             _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/input_layer4", (1, 2, 3),
                             _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/input_layer5", (1, 1, 4),
                             _FakeFormatType.FLOAT32),
        ]
        infos_out = [_FakeVStreamInfo("net/conv49", (1, 1, 3),
                                      _FakeFormatType.FLOAT32)]
        seen = {}

        def transform(b):
            for name in ("net/input_layer1", "net/input_layer2",
                         "net/input_layer3"):
                seen[name] = np.asarray(b.input(name).get_buffer()).copy()
            _first_row_transform("net/input_layer4", "net/conv49")(b)

        _install_fake_hailo(self, infos_in, infos_out, transform)
        frames = self._frames(3)
        frames[0][:] = 10
        frames[1][:] = 20
        frames[2][:] = 30
        with HailoPredictor("fake.hef") as p:
            p.predict(frames, np.zeros((2, 3), dtype=np.float32))
        np.testing.assert_array_equal(
            seen["net/input_layer1"], np.full((8, 8, 3), 10, np.float32))
        np.testing.assert_array_equal(
            seen["net/input_layer2"], np.full((8, 8, 3), 20, np.float32))
        np.testing.assert_array_equal(
            seen["net/input_layer3"], np.full((8, 8, 3), 30, np.float32))

    def _fake_five_input_hef(self, q_frame):
        infos_in = [
            _FakeVStreamInfo("net/input_layer1", (8, 8, 3),
                             _FakeFormatType.UINT8, q_frame),
            _FakeVStreamInfo("net/input_layer2", (8, 8, 3),
                             _FakeFormatType.UINT8, q_frame),
            _FakeVStreamInfo("net/input_layer3", (8, 8, 3),
                             _FakeFormatType.UINT8, q_frame),
            _FakeVStreamInfo("net/input_layer4", (1, 5, 18),
                             _FakeFormatType.UINT8,
                             _FakeQuantInfo(qp_zp=0.0, qp_scale=1.960784)),
            _FakeVStreamInfo("net/input_layer5", (1, 1, 128),
                             _FakeFormatType.UINT8,
                             _FakeQuantInfo(qp_zp=123.0, qp_scale=0.016)),
        ]
        infos_out = [_FakeVStreamInfo(
            "net/conv49", (1, 1, 18), _FakeFormatType.UINT8,
            _FakeQuantInfo(qp_zp=2.0, qp_scale=0.837115))]
        seen = {}

        def transform(b):
            seen["f0"] = np.asarray(
                b.input("net/input_layer1").get_buffer()).copy()
            b.output("net/conv49").get_buffer()[...] = 2

        _install_fake_hailo(self, infos_in, infos_out, transform)
        return seen

    def test_frame_normalized_when_quant_grid_covers_negatives(self):
        """帧输入归一化判定来自量化网格：dequant(0)≈-2.1（实测 HEF
        zp=114, scale=0.0187）⇒ 须先 ImageNet 归一化再量化。
        回归：旧启发式 zp*scale<0 将其误判为原始像素直通，图像路径
        被饱和破坏（实机 max_diff 2.55 → 修复后 0.37）。"""
        from hailo_infer import HailoPredictor
        seen = self._fake_five_input_hef(
            _FakeQuantInfo(qp_zp=114.0, qp_scale=0.018723))
        frame = np.full((8, 8, 3), 128, dtype=np.uint8)
        with HailoPredictor("fake.hef") as p:
            self.assertTrue(p._normalize_frames)
            p.predict([frame] * 3, np.zeros((5, 18), np.float32))
        # 中灰 128 → 归一化 (128/255-mean)/std → 量化码 ≈ [118, 125, 137]
        np.testing.assert_array_equal(
            seen["f0"][:, :, 0], np.full((8, 8), 118, np.uint8))
        np.testing.assert_array_equal(
            seen["f0"][:, :, 1], np.full((8, 8), 125, np.uint8))
        np.testing.assert_array_equal(
            seen["f0"][:, :, 2], np.full((8, 8), 137, np.uint8))

    def test_frame_raw_when_quant_grid_is_pixel_range(self):
        """原始像素校准（zp=0, scale=1）⇒ 帧直通不归一化"""
        from hailo_infer import HailoPredictor
        seen = self._fake_five_input_hef(
            _FakeQuantInfo(qp_zp=0.0, qp_scale=1.0))
        frame = np.full((8, 8, 3), 128, dtype=np.uint8)
        with HailoPredictor("fake.hef") as p:
            self.assertFalse(p._normalize_frames)
            p.predict([frame] * 3, np.zeros((5, 18), np.float32))
        np.testing.assert_array_equal(
            seen["f0"], np.full((8, 8, 3), 128, np.uint8))

    def test_frame_count_mismatch_raises(self):
        from hailo_infer import HailoPredictor
        infos_in = [
            _FakeVStreamInfo("net/images", (1, 2, 3, 4, 4), _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/history", (1, 2, 3), _FakeFormatType.FLOAT32),
        ]
        infos_out = [_FakeVStreamInfo("net/action", (1, 3), _FakeFormatType.FLOAT32)]
        _install_fake_hailo(self, infos_in, infos_out,
                            _first_row_transform("net/history", "net/action"))
        with HailoPredictor("fake.hef") as p:
            with self.assertRaises(ValueError):
                p.predict(self._frames(1), np.zeros((2, 3), dtype=np.float32))

    def test_output_dim_mismatch_raises(self):
        from hailo_infer import HailoPredictor
        infos_in = [
            _FakeVStreamInfo("net/images", (1, 2, 3, 4, 4), _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/history", (1, 2, 3), _FakeFormatType.FLOAT32),
        ]
        infos_out = [_FakeVStreamInfo("net/action", (1, 4), _FakeFormatType.FLOAT32)]
        _install_fake_hailo(self, infos_in, infos_out, lambda b: None)
        with self.assertRaises(ValueError):
            HailoPredictor("fake.hef")

    def test_stats_normalization_roundtrip(self):
        from hailo_infer import HailoPredictor
        infos_in = [
            _FakeVStreamInfo("net/images", (1, 2, 3, 4, 4), _FakeFormatType.FLOAT32),
            _FakeVStreamInfo("net/history", (1, 2, 3), _FakeFormatType.FLOAT32),
        ]
        infos_out = [_FakeVStreamInfo("net/action", (1, 3), _FakeFormatType.FLOAT32)]
        _install_fake_hailo(self, infos_in, infos_out,
                            _first_row_transform("net/history", "net/action"))
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_path = os.path.join(tmpdir, "action_stats.npy")
            np.save(stats_path, np.stack([np.full(3, 1.0), np.full(3, 2.0)]))
            history = np.array([[3, 3, 3], [1, 1, 1]], dtype=np.float32)
            with HailoPredictor("fake.hef", stats_path=stats_path) as p:
                result = p.predict(self._frames(2), history)
        np.testing.assert_allclose(result, [3, 3, 3], atol=1e-6)


class TestDfcCompile(unittest.TestCase):
    """dfc_compile.py 的 HAR 输入层连通性检查（编译前拦截输入断连）"""

    @staticmethod
    def _make_har(layers):
        import json
        import tarfile
        data = json.dumps(
            {"name": "t", "net_params": {}, "layers": layers}).encode()
        path = tempfile.mktemp(suffix=".har")
        with tarfile.open(path, "w") as tf:
            ti = tarfile.TarInfo("net.hn")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        return path

    @staticmethod
    def _layers(connect_input2=True):
        return {
            "n/input_layer1": {"type": "input_layer", "input": [],
                               "output": ["n/conv1"]},
            "n/input_layer2": {"type": "input_layer", "input": [],
                               "output": ["n/conv2"]},
            "n/conv1": {"type": "conv", "input": ["n/input_layer1"],
                        "output": ["n/out"]},
            "n/conv2": {"type": "conv", "input": ["n/input_layer2"],
                        "output": ["n/out" if connect_input2 else "n/dead"]},
            "n/dead": {"type": "conv", "input": ["n/conv2"], "output": []},
            "n/out": {"type": "output_layer",
                      "input": ["n/conv1"] + (["n/conv2"] if connect_input2 else []),
                      "output": []},
        }

    def test_input_layer_names_sorted_by_suffix(self):
        from dfc_compile import har_input_layer_names
        har = self._make_har(self._layers())
        try:
            names = har_input_layer_names(har)
        finally:
            os.unlink(har)
        self.assertEqual(names, ["n/input_layer1", "n/input_layer2"])

    def test_connectivity_pass(self):
        from dfc_compile import check_input_connectivity
        har = self._make_har(self._layers())
        try:
            check_input_connectivity(har, ["n/input_layer1", "n/input_layer2"])
        finally:
            os.unlink(har)

    def test_disconnected_input_blocked(self):
        from dfc_compile import check_input_connectivity
        har = self._make_har(self._layers(connect_input2=False))
        try:
            with self.assertRaises(SystemExit) as ctx:
                check_input_connectivity(har, ["n/input_layer1", "n/input_layer2"])
        finally:
            os.unlink(har)
        self.assertIn("n/input_layer2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
