"""Public-package invariants for the source-built v07 asset set (no compiler/network)."""
from paths import ROOT
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(ROOT / 'tools'))
import build_fsr4


class Fsr4PackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'assets'
        self.source.mkdir()
        self.destination = Path(self.tmp.name) / 'package'
        for name in build_fsr4.expected_assets():
            if name.endswith('.spv'):
                data = struct.pack('<5I', 0x07230203, 0x00010600, 0, 1, 0)
            elif name.endswith('_initializers.bin'):
                model = name.removeprefix('fsr4_model_v07_i8_').removesuffix('_initializers.bin')
                data = self.model_dir(model).joinpath('initializers.bin').read_bytes()
            elif name.endswith('_pre_weights.bin'):
                model = name.removeprefix('fsr4_model_v07_i8_').removesuffix('_pre_weights.bin')
                source = self.model_dir(model).joinpath('pre.hlsl').read_text()
                array = re.search(r'embedded_encoder1_DownscaleStridedConv2x2_downscale_conv_weight_dwords\[256\]\s*=\s*\{(.*?)\};', source, re.S)
                data = struct.pack('<256I', *(int(value, 16) for value in re.findall(r'0x[0-9a-fA-F]+', array[1])))
            else:
                data = b'{}\n'
            self.source.joinpath(name).write_bytes(data)
        self.manifest = {'format': 1, 'source_sha256': build_fsr4.source_digest(),
                         'files': {name: self.record(name) for name in build_fsr4.expected_assets()}}
        self.save_manifest()

    def model_dir(self, model):
        return build_fsr4.VENDOR / f'sdk/Kits/FidelityFX/upscalers/fsr4/internal/shaders/fsr4_model_v07_i8_{model}'

    def record(self, name):
        data = self.source.joinpath(name).read_bytes()
        return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def save_manifest(self):
        self.source.joinpath('build.json').write_text(json.dumps(self.manifest))

    def test_public_package_excludes_local_binary_and_optimized_overrides(self):
        self.source.joinpath('unreviewed.dll').write_bytes(b'local-only')
        self.source.joinpath('opt').mkdir()
        self.source.joinpath('opt/rcas.spv').write_bytes(b'local override')
        build_fsr4.stage_assets(self.source, self.destination)
        staged = self.destination / 'fsr4_shaders'
        self.assertFalse((staged / 'unreviewed.dll').exists())
        self.assertFalse((staged / 'opt').exists())
        self.assertEqual(set(p.name for p in staged.iterdir()), build_fsr4.expected_assets() | {'build.json'})
        licenses = self.destination / 'licenses'
        self.assertIn('Permission is hereby granted', (licenses / 'FSR4-AMD-MIT.txt').read_text())
        self.assertIn('GNU GENERAL PUBLIC LICENSE', (licenses / 'FSR4-Q2RTX-GPL.txt').read_text())
        self.assertIn('Complete corresponding shader source', (licenses / 'FSR4-NOTICE.txt').read_text())

    def test_tampered_shader_is_rejected_before_package_is_created(self):
        self.source.joinpath('rcas.spv').write_bytes(b'bad shader')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch: rcas.spv'):
            build_fsr4.stage_assets(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_inventory_cannot_authorize_unreviewed_binary(self):
        self.source.joinpath('extra.dll').write_bytes(b'not a model asset')
        self.manifest['files']['extra.dll'] = self.record('extra.dll')
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            build_fsr4.stage_assets(self.source, self.destination)

    def test_model_weights_cannot_be_swapped_even_with_updated_output_checksum(self):
        name = 'fsr4_model_v07_i8_quality_initializers.bin'
        self.source.joinpath(name).write_bytes(self.model_dir('native').joinpath('initializers.bin').read_bytes())
        self.manifest['files'][name] = self.record(name)
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'weights differ from the pinned source'):
            build_fsr4.stage_assets(self.source, self.destination)

    def test_incomplete_inventory_is_rejected(self):
        del self.manifest['files']['fsr4_model_v07_i8_native_1080_pass12.spv']
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            build_fsr4.stage_assets(self.source, self.destination)

    def test_stale_source_build_is_rejected(self):
        self.manifest['source_sha256'] = '0' * 64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_fsr4.stage_assets(self.source, self.destination)


if __name__ == '__main__':
    unittest.main()
