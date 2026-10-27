# SPDX-License-Identifier: GPL-3.0-only
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from panel import install, recovery
from prebuilt import asset_name

CONFIG = json.loads((install.ROOT / 'release.json').read_text())
PACKAGES = {'cosmic-panel': 'cosmic-panel 1:1.10.0-1'}


class PanelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='forge panel ')
        self.addCleanup(temp.cleanup)
        self.prefix = Path(temp.name)
        self.binary = self.prefix / 'input'
        self.binary.write_bytes(b'\x7fELFforge-panel-engine-v1')
        self.enterContext(patch.object(install.subprocess, 'check_output', return_value=CONFIG['sources']['1.10.0'] + '-forge-panel-r1'))
        self.enterContext(patch.object(install, 'package_manager', return_value='pacman'))
        self.system = self.prefix / 'system'
        self.system.write_bytes(b'\x7fELFstock')
        self.system.chmod(0o755)
        self.enterContext(patch.object(recovery, 'SYSTEM_PANEL', self.system))

    def apply(self):
        install.install(self.binary, PACKAGES, '1.10.0', self.prefix)

    def test_install_skip_and_stock_restore_are_independent(self):
        other = self.prefix / 'bin/cosmic-settings'
        other.parent.mkdir()
        other.write_text('existing Settings')
        comp = other.with_name('cosmic-comp')
        comp.write_text('existing compositor')
        self.apply()
        record = self.prefix / 'state/cosmic-forge/panel-installation.json'
        before = record.read_bytes(), record.stat().st_mtime_ns
        args = SimpleNamespace(prefix=self.prefix, restore=False, binary=None, build=False, release_tag=None)
        with patch.object(install, 'installed_packages', return_value=(PACKAGES, '1.10.0')), patch.object(install, 'obtain', side_effect=AssertionError('must skip download')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                install.run(args)
        self.assertIn('already_installed', output.getvalue())
        self.assertEqual((record.read_bytes(), record.stat().st_mtime_ns), before)
        recovery.restore(self.prefix)
        self.assertFalse((self.prefix / 'bin/cosmic-panel').exists())
        self.assertEqual(other.read_text(), 'existing Settings')
        self.assertEqual(comp.read_text(), 'existing compositor')

    def test_unowned_or_modified_launcher_is_preserved(self):
        launcher = self.prefix / 'bin/cosmic-panel'
        launcher.parent.mkdir()
        launcher.write_text('user launcher')
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(launcher.read_text(), 'user launcher')
        launcher.unlink()
        self.apply()
        launcher.write_text('changed')
        with self.assertRaises(RuntimeError):
            recovery.restore(self.prefix)
        self.assertEqual(launcher.read_text(), 'changed')

    def test_package_changes_during_download_do_not_install(self):
        args = SimpleNamespace(prefix=self.prefix, restore=False, binary=None, build=False, release_tag=None)
        with patch.object(install, 'installed_packages', side_effect=[(PACKAGES, '1.10.0'), ({'cosmic-panel':'changed'}, '1.10.0')]), patch.object(install, 'obtain', return_value=self.binary):
            with self.assertRaises(RuntimeError):
                install.run(args)
        self.assertFalse((self.prefix / 'bin/cosmic-panel').exists())

    def test_rejects_an_old_panel_revision(self):
        with patch.object(install.subprocess, 'check_output', return_value=CONFIG['sources']['1.10.0'] + '-forge-panel-r0'):
            with self.assertRaises(RuntimeError):
                self.apply()
        self.assertFalse((self.prefix / 'bin/cosmic-panel').exists())

    def test_separate_panel_asset(self):
        self.assertEqual(asset_name('1.10.0', 'aarch64', 'panel'), 'cosmic-panel-1.10.0-linux-aarch64.xz')
