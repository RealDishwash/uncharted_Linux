"""Tests for update source pinning and release version comparison.
Run: python3 -m unittest discover -s tests"""
import io
import json
import os
import sys
import unittest
import urllib.request
from unittest import mock

from paths import ROOT

if str(ROOT / 'launcher') not in sys.path:
    sys.path.insert(0, str(ROOT / 'launcher'))
import uncharted_launcher as launcher
import uncharted_linux as linux

FORK_REPO = 'RealDishwash/uncharted_Linux'
FOREIGN_REPOS = [
    'spacepepperoni/uncharted_Linux',
    'Supermedo/uncharted_pc',
    'Supermedo/bloodborne_pc',
    'deadinside28/bloodborne_pc',
]


class UpdateSourceTests(unittest.TestCase):
    def test_configured_update_urls_point_to_fork_only(self):
        self.assertEqual(
            launcher.RELEASES_API,
            f'https://api.github.com/repos/{FORK_REPO}/releases/latest',
        )
        self.assertEqual(
            launcher.RELEASES_PAGE,
            f'https://github.com/{FORK_REPO}/releases/latest',
        )
        self.assertEqual(linux.REPO_URL, f'https://github.com/{FORK_REPO}')
        self.assertEqual(linux.UPSTREAM_ISSUES, f'https://github.com/{FORK_REPO}/issues')

        for foreign in FOREIGN_REPOS:
            self.assertNotIn(foreign, launcher.RELEASES_API)
            self.assertNotIn(foreign, launcher.RELEASES_PAGE)
            self.assertNotIn(foreign, linux.REPO_URL)
            self.assertNotIn(foreign, linux.UPSTREAM_ISSUES)

    def test_latest_release_queries_fork_and_fails_on_foreign_repo(self):
        fake_payload = json.dumps({
            'tag_name': 'v0.3.6-linux.42',
            'html_url': f'https://github.com/{FORK_REPO}/releases/tag/v0.3.6-linux.42',
            'body': '<!-- update notes:\n- Performance boost\n-->',
            'assets': [{
                'name': 'Uncharted-x86_64.AppImage',
                'browser_download_url': f'https://github.com/{FORK_REPO}/releases/download/v0.3.6-linux.42/Uncharted-x86_64.AppImage',
            }],
        }).encode('utf-8')

        captured_requests = []

        def fake_urlopen(req, timeout=15):
            captured_requests.append(req)
            # Fail if any foreign repository is being queried
            for foreign in FOREIGN_REPOS:
                if foreign in req.full_url:
                    raise AssertionError(f'Foreign repo queried: {req.full_url}')
            return io.BytesIO(fake_payload)

        with mock.patch('urllib.request.urlopen', side_effect=fake_urlopen):
            version, url, page, notes = launcher.latest_release()

        self.assertEqual(len(captured_requests), 1)
        req = captured_requests[0]
        self.assertEqual(req.full_url, f'https://api.github.com/repos/{FORK_REPO}/releases/latest')
        self.assertIn(FORK_REPO, req.full_url)
        for foreign in FOREIGN_REPOS:
            self.assertNotIn(foreign, req.full_url)

        self.assertEqual(version, '0.3.6-linux.42')
        self.assertIn('Uncharted-x86_64.AppImage', url)
        self.assertIn(FORK_REPO, url)
        self.assertIn(FORK_REPO, page)

    def test_crash_report_url_points_to_fork_issues_only(self):
        report_url = linux.crash_report_url('SIGSEGV in rendering loop', 'Run: ./Uncharted\nCrash details')
        self.assertTrue(report_url.startswith(f'https://github.com/{FORK_REPO}/issues/new?'))
        for foreign in FOREIGN_REPOS:
            self.assertNotIn(foreign, report_url)


class VersionComparisonTests(unittest.TestCase):
    def test_version_key_ordering_with_ci_run_numbers(self):
        # CI tags format: v<upstream>-linux.<run>
        self.assertGreater(linux.version_key('v0.3.6-linux.12'), linux.version_key('v0.3.6-linux.5'))
        self.assertGreater(linux.version_key('v0.3.6-linux.10'), linux.version_key('v0.3.6-linux.9'))
        self.assertGreater(linux.version_key('v0.3.7-linux.1'), linux.version_key('v0.3.6-linux.99'))
        self.assertGreater(linux.version_key('0.4.1-linux.60'), linux.version_key('0.4-linux.57'))

    def test_version_key_handles_v_prefix_consistently(self):
        self.assertEqual(linux.version_key('v0.3.6-linux.12'), linux.version_key('0.3.6-linux.12'))
        self.assertGreater(linux.version_key('v0.3.6-linux.1'), linux.version_key('0.3.6'))
        self.assertEqual(linux.version_key('0.3.6'), (0, 3, 6, 0, 0))
        self.assertEqual(linux.version_key('v0.3.6-linux.1'), (0, 3, 6, 0, 1))

    def test_launcher_version_tuple_delegates_to_version_key(self):
        self.assertEqual(launcher.version_tuple('v0.3.6-linux.12'), linux.version_key('v0.3.6-linux.12'))
        self.assertGreater(launcher.version_tuple('v0.3.6-linux.12'), launcher.version_tuple('v0.3.6-linux.5'))


class BackgroundUpdateTests(unittest.TestCase):
    def test_background_update_triggers_only_for_newer_version(self):
        installed = []
        with mock.patch.dict(os.environ, {'APPIMAGE': '/tmp/test.AppImage'}):
            with mock.patch.object(linux, 'install_appimage', side_effect=lambda url, path: installed.append((url, path))):
                # When newer version available:
                release_info = ('0.3.6-linux.50', 'https://github.com/RealDishwash/uncharted_Linux/releases/download/v0.3.6-linux.50/Uncharted-x86_64.AppImage', '', '')
                thread = linux.update_in_background(lambda: release_info, launcher.version_tuple, '0.3.6-linux.10', {'check_updates': True})
                self.assertIsNotNone(thread)
                thread.join(timeout=5.0)
                self.assertEqual(len(installed), 1)

                # When older or equal version available:
                installed.clear()
                thread = linux.update_in_background(lambda: release_info, launcher.version_tuple, '0.3.6-linux.50', {'check_updates': True})
                self.assertIsNotNone(thread)
                thread.join(timeout=5.0)
                self.assertEqual(len(installed), 0)


if __name__ == '__main__':
    unittest.main()
