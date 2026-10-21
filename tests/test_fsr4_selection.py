"""Selection and asset-directory precedence at the launcher's real runtime boundary."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from paths import ROOT

sys.path.insert(0, str(ROOT / 'launcher'))
import uncharted_launcher as launcher


class Fsr4SelectionAndDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.port = root / 'port'
        self.data = root / 'data'
        self.port.mkdir()
        self.data.mkdir()
        for context in (patch.dict(os.environ, {}, clear=True),
                        patch.object(launcher, 'PORT_DIR', self.port),
                        patch.object(launcher, 'DATA_DIR', self.data)):
            context.start()
            self.addCleanup(context.stop)

    def environment(self, **settings):
        return launcher.game_environment({**launcher.APP_DEFAULTS, **settings})

    def test_deck_is_opt_in_and_preserves_v07_selection_but_not_proprietary_models(self):
        self.assertEqual(self.environment(steam_deck=True)['BB_UPSCALER'], 'off')
        self.assertEqual(self.environment(steam_deck=True, anti_aliasing='fsr4')['BB_UPSCALER'], 'fsr4')
        self.assertEqual(self.environment(steam_deck=True, anti_aliasing='fsr411')['BB_UPSCALER'], 'off')
        self.assertEqual(self.environment(steam_deck=False, anti_aliasing='fsr411')['BB_UPSCALER'], 'fsr411')

    def test_package_assets_take_precedence_over_user_data_copy(self):
        (self.port / 'fsr4_shaders').mkdir()
        (self.data / 'fsr4_shaders').mkdir()
        env = self.environment(anti_aliasing='fsr4')
        self.assertEqual(env['BB_FSR4_DIR'], str(self.port / 'fsr4_shaders'))

    def test_user_data_copy_is_found_when_package_has_no_assets(self):
        (self.data / 'fsr4_shaders').mkdir()
        env = self.environment(anti_aliasing='fsr4')
        self.assertEqual(env['BB_FSR4_DIR'], str(self.data / 'fsr4_shaders'))

    def test_explicit_environment_override_is_not_replaced_by_package(self):
        (self.port / 'fsr4_shaders').mkdir()
        # Even a missing explicit path must remain explicit, rather than silently selecting
        # different weights from the package.
        custom = self.port / 'missing-custom-assets'
        os.environ['BB_FSR4_DIR'] = str(custom)
        self.assertEqual(self.environment(anti_aliasing='fsr4')['BB_FSR4_DIR'], str(custom))

    def test_launcher_extra_environment_has_final_precedence(self):
        (self.port / 'fsr4_shaders').mkdir()
        os.environ['BB_FSR4_DIR'] = str(self.data / 'from-process')
        custom = self.data / 'from-launcher'
        env = self.environment(anti_aliasing='fsr4', extra_env=f'BB_FSR4_DIR={custom}')
        self.assertEqual(env['BB_FSR4_DIR'], str(custom))


if __name__ == '__main__':
    unittest.main()
