import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from bbport_assets import fsr4_problem, fsr411_problem


class UpscalerAssetsTests(unittest.TestCase):
    def test_tier_depends_on_output_not_render_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIn('t2160_m0/spd.spv', fsr411_problem(root, '3840x2160', 3))
            self.assertIn('t2160_m0/spd.spv', fsr411_problem(root, '2560x1440', 0))
            self.assertIn('t1080_m1/spd.spv', fsr411_problem(root, '1280x720', 4))

    def test_partial_model_is_not_reported_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 't2160_m0'
            folder.mkdir()
            (folder / 'spd.spv').write_bytes(bytes(20))
            self.assertIn('prepass.spv', fsr411_problem(root, '3840x2160', 0))

    def test_complete_model_and_wrong_initializer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 't1080_m0'
            folder.mkdir()
            names = ['spd', 'prepass', 'pass0_post', 'postpass', 'rcas']
            for i in range(1, 13):
                names.extend([f'pass{i}', f'pass{i}_post'])
            for name in names:
                (folder / (name+'.spv')).write_bytes(bytes(20))
            (folder / 'initializer.bin').write_bytes(bytes(8))
            self.assertIn('initializer.bin', fsr411_problem(root, '1920x1080', 1))
            (folder / 'initializer.bin').write_bytes(bytes(131072))
            self.assertIsNone(fsr411_problem(root, '1920x1080', 1))

    def test_fsr4_tier_depends_on_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'rcas.spv').write_bytes(bytes(20))
            (root / 'spd_auto_exposure.spv').write_bytes(bytes(20))
            # 1080p output selects 1080 tier
            self.assertIn('fsr4_model_v07_i8_native_1080_pre.spv', fsr4_problem(root, '1920x1080', 0))
            # 1440p and 4K select 2160 tier
            self.assertIn('fsr4_model_v07_i8_quality_2160_pre.spv', fsr4_problem(root, '2560x1440', 1))
            self.assertIn('fsr4_model_v07_i8_quality_2160_pre.spv', fsr4_problem(root, '3840x2160', 1))
            # Outputs exceeding 4K select 4320 tier
            self.assertIn('fsr4_model_v07_i8_quality_4320_pre.spv', fsr4_problem(root, '5120x2880', 1))
            self.assertIn('fsr4_model_v07_i8_balanced_4320_pre.spv', fsr4_problem(root, '7680x4320', 2))

    def test_fsr4_partial_model_is_not_reported_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'rcas.spv').write_bytes(bytes(20))
            (root / 'spd_auto_exposure.spv').write_bytes(bytes(20))
            (root / 'fsr4_model_v07_i8_native_1080_pre.spv').write_bytes(bytes(20))
            self.assertIn('fsr4_model_v07_i8_native_1080_post.spv', fsr4_problem(root, '1920x1080', 0))

    def test_fsr4_invalid_spv_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'rcas.spv').write_bytes(bytes(17))  # not a multiple of 4
            self.assertIn('rcas.spv', fsr4_problem(root, '1920x1080', 0))

    def test_fsr4_wrong_initializer_or_weights_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'rcas.spv').write_bytes(bytes(20))
            (root / 'spd_auto_exposure.spv').write_bytes(bytes(20))
            (root / 'fsr4_model_v07_i8_native_1080_pre.spv').write_bytes(bytes(20))
            (root / 'fsr4_model_v07_i8_native_1080_post.spv').write_bytes(bytes(20))
            for i in range(1, 13):
                (root / f'fsr4_model_v07_i8_native_1080_pass{i}.spv').write_bytes(bytes(20))
            (root / 'fsr4_model_v07_i8_native_initializers.bin').write_bytes(bytes(100))
            self.assertIn('initializers.bin', fsr4_problem(root, '1920x1080', 0))
            (root / 'fsr4_model_v07_i8_native_initializers.bin').write_bytes(bytes(89216))
            (root / 'fsr4_model_v07_i8_native_pre_weights.bin').write_bytes(bytes(100))
            self.assertIn('pre_weights.bin', fsr4_problem(root, '1920x1080', 0))
            (root / 'fsr4_model_v07_i8_native_pre_weights.bin').write_bytes(bytes(1024))
            self.assertIsNone(fsr4_problem(root, '1920x1080', 0))

    def test_fsr4_opt_pass_fallback(self):
        override = patch.dict('os.environ', {'BB_FSR4_OPT': '1'})
        override.start()
        self.addCleanup(override.stop)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opt = root / 'opt'
            opt.mkdir()
            (root / 'rcas.spv').write_bytes(bytes(20))
            (root / 'spd_auto_exposure.spv').write_bytes(bytes(20))
            (root / 'fsr4_model_v07_i8_native_1080_pre.spv').write_bytes(bytes(20))
            (opt / 'fsr4_model_v07_i8_native_1080_post.spv').write_bytes(bytes(20))  # located in opt/
            for i in range(1, 13):
                (root / f'fsr4_model_v07_i8_native_1080_pass{i}.spv').write_bytes(bytes(20))
            (root / 'fsr4_model_v07_i8_native_initializers.bin').write_bytes(bytes(89216))
            (root / 'fsr4_model_v07_i8_native_pre_weights.bin').write_bytes(bytes(1024))
            self.assertIsNone(fsr4_problem(root, '1920x1080', 0))
            # Runtime prefers a valid optimized pass even if the original is corrupt.
            (root / 'fsr4_model_v07_i8_native_1080_post.spv').write_bytes(bytes(17))
            self.assertIsNone(fsr4_problem(root, '1920x1080', 0))
            with patch.dict('os.environ', {'BB_FSR4_OPT': '0'}):
                self.assertIn('post.spv', fsr4_problem(root, '1920x1080', 0))
