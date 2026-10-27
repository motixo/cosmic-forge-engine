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
    def test_native_package_managers_capture_exact_versions(self):
        for identity, manager, version in [('arch', 'pacman', '1:1.10.0-1'),
                                            ('fedora', 'rpm', '0:1.10.0-2.fc43'),
                                            ('ubuntu', 'dpkg-query', '1.10.0-1ubuntu1'),
                                            ('pop', 'dpkg-query', '1.10.0-1pop1')]:
            with self.subTest(identity=identity):
                def query(command, **_):
                    self.assertEqual(command[0], manager)
                    value = command[-1] + ' ' + version + (' installed' if manager == 'dpkg-query' else '')
                    return subprocess.CompletedProcess(command, 0, stdout=value)
                with patch('platforms.platform.freedesktop_os_release', return_value={'ID': identity}), \
                     patch('platforms.subprocess.run', side_effect=query):
                    packages, cosmic = platforms.installed_packages()
                self.assertEqual(cosmic, '1.10.0')
                self.assertEqual(len(packages), 4)
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
