# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import platforms

PIN = json.loads((ROOT / 'release.json').read_text())['sources']['1.10.0']


class PlatformTests(unittest.TestCase):
    def test_pop_noble_compositor_snapshots_are_supported(self):
        for build, commit in [('1794310577', '66524a0'), ('1794501573', '8129317')]:
            with self.subTest(commit=commit):
                self.assertEqual(
                    platforms.compositor_version(f'cosmic-comp 0.1~{build}~24.04~{commit} installed'),
                    f'1.11.0-pop.{commit}',
                )

    def test_native_package_managers_capture_exact_versions(self):
        for identity, manager, version in [('arch', 'pacman', '1:1.10.0-1'),
                                            ('fedora', 'rpm', '0:1.10.0-2.fc43'),
                                            ('ubuntu', 'dpkg-query', '1.10.0-1ubuntu1'),
                                            ('pop', 'dpkg-query', '1.10.0-1pop1')]:
            with self.subTest(identity=identity):
                def query(command, **_):
                    self.assertEqual(command[0], manager)
                    self.assertEqual(command[-1], 'cosmic-comp')
                    value = command[-1] + ' ' + version + (' installed' if manager == 'dpkg-query' else '')
                    return subprocess.CompletedProcess(command, 0, stdout=value)
                with patch('platforms.platform.freedesktop_os_release', return_value={'ID': identity}), \
                     patch('platforms.subprocess.run', side_effect=query):
                    packages, cosmic = platforms.installed_packages()
                self.assertEqual(cosmic, '1.10.0')
                self.assertEqual(set(packages), {'cosmic-comp'})
                self.assertIn(version, packages['cosmic-comp'])

    def test_derivatives_use_the_native_package_manager(self):
        for identity, like, manager in [('nobara', 'fedora', 'rpm'), ('pop', 'ubuntu debian', 'dpkg-query'),
                                        ('endeavouros', 'arch', 'pacman')]:
            with patch('platforms.platform.freedesktop_os_release', return_value={'ID': identity, 'ID_LIKE': like}):
                self.assertEqual(platforms.package_manager(), manager)

    def test_pop_snapshot_versions_resolve_the_pinned_compositor_commit(self):
        self.assertEqual(platforms.compositor_version('cosmic-comp 0.1~1780000000~24.04~' + PIN[:7] + ' installed'), '1.10.0')
        with patch('platforms.subprocess.check_output', return_value='cosmic-comp ' + PIN):
            self.assertEqual(platforms.compositor_version('cosmic-comp 0.1~1780000000~24.04 installed'), '1.10.0')

    def test_pop_compositor_snapshot_is_selected_independently_of_the_session_version(self):
        self.assertEqual(platforms.compositor_version(
            'cosmic-comp 0.1~1793802426~24.04~ec674cd installed'), '1.11.0-pop.ec674cd')
        # A future semantic package label must still respect its actual snapshot commit.
        self.assertEqual(platforms.compositor_version(
            'cosmic-comp 1.11.0~1793802426~24.04~ec674cd installed'), '1.11.0-pop.ec674cd')

    def test_unknown_snapshot_cannot_fall_back_to_a_stale_binary_or_epoch_label(self):
        for label in ('0.1', '1.11.0'):
            package = f'cosmic-comp {label}~1793802426~24.04~abcdef0 installed'
            with patch('platforms.subprocess.check_output', return_value='cosmic-comp ' + PIN) as query:
                with self.assertRaisesRegex(RuntimeError, r'installed cosmic-comp .*abcdef0'):
                    platforms.compositor_version(package)
                query.assert_not_called()

    def test_unknown_or_unpublished_versions_are_refused(self):
        for version in ['1.9.0-1', '1.12.0-1', '2.0.0-1', '0.1~abc1234']:
            with patch('platforms.subprocess.check_output', return_value='unknown revision'):
                with self.assertRaises(RuntimeError):
                    platforms.compositor_version('cosmic-comp ' + version)
        with patch('platforms.platform.freedesktop_os_release', return_value={'ID': 'unknown'}):
            with self.assertRaises(RuntimeError):
                platforms.package_manager()

    def test_dpkg_requires_installed_packages(self):
        with patch('platforms.platform.freedesktop_os_release', return_value={'ID': 'ubuntu'}), \
             patch('platforms.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout='cosmic-comp 1.10.0-1 config-files')):
            with self.assertRaises(RuntimeError):
                platforms.installed_packages()
