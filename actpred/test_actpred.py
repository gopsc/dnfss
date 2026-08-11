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
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image
import torch

from config import (Config, DataConfig, ModelConfig, build_argparser,
                    apply_overrides, validate_config_values, parse_checkpoint_config)
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
        data_cfg = DataConfig(image_size=64, action_dim=18, history_len=5, frame_len=3)
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
        data_cfg = DataConfig(image_size=32, action_dim=4, history_len=3, frame_len=1)
        model_cfg = ModelConfig(cnn_backbone="simple")
        model = ActionPredictor(model_cfg, data_cfg)
        model.eval()

        images = torch.randn(2, 1, 3, 32, 32)
        history = torch.randn(2, 3, 4)
        with torch.no_grad():
            out = model(images, history)
        self.assertEqual(out.shape, (2, 4))

    def test_forward_dynamic_batch(self):
        data_cfg = DataConfig(image_size=32, action_dim=4, history_len=3, frame_len=2)
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
        data_cfg = DataConfig(image_size=32, action_dim=4, history_len=3, frame_len=2)
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
            ds = ActionDataset(tmpdir, history_len=3, action_dim=4, image_size=32,
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
            ds = ActionDataset(tmpdir, history_len=3, action_dim=4, image_size=32,
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


class TestConfig(unittest.TestCase):
    """测试配置和 CLI 参数"""

    def test_default_config(self):
        cfg = Config()
        self.assertEqual(cfg.data.image_size, 224)
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
        for field, value in [("image_size", 99999), ("image_size", 0),
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
    def _make_checkpoint(tmpdir, normalize_actions=False, image_size=32,
                         frame_len=3):
        cfg = Config()
        cfg.data.image_size = image_size
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
            cfg_dict["data"]["image_size"] = 99999
            bad_path = os.path.join(tmpdir, "bad.pt")
            torch.save({"model_state_dict": model.state_dict(),
                        "config": cfg_dict}, bad_path)
            with self.assertRaisesRegex(ValueError, "image_size"):
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




if __name__ == "__main__":
    unittest.main(verbosity=2)
