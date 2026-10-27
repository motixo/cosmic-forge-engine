# SPDX-License-Identifier: GPL-3.0-only
import contextlib
import io
import json
import shlex
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from greeter import install, recovery
from prebuilt import asset_name

CONFIG = json.loads((install.ROOT / 'release.json').read_text())
PACKAGES = {'cosmic-greeter': 'cosmic-greeter 1:1.10.0-1'}


class GreeterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='forge greeter ')
        self.addCleanup(temp.cleanup)
        self.prefix = Path(temp.name)
        self.binary = self.prefix / 'input'
        self.binary.write_bytes(b'\x7fELFforge-lockscreen-engine-v1')
        self.enterContext(patch.object(install.subprocess, 'check_output', return_value=CONFIG['sources']['1.10.0'] + '-forge-greeter-r1'))
        self.enterContext(patch.object(install, 'package_manager', return_value='pacman'))
        self.system = self.prefix / 'system'
        self.system.write_bytes(b'\x7fELFstock')
        self.system.chmod(0o755)
        self.enterContext(patch.object(recovery, 'SYSTEM_GREETER', self.system))

    def apply(self):
        install.install(self.binary, PACKAGES, '1.10.0', self.prefix)

    def test_install_skip_and_stock_restore_are_independent(self):
        other = self.prefix / 'bin/cosmic-settings'
        other.parent.mkdir()
        other.write_text('existing Settings')
        comp = other.with_name('cosmic-comp')
        comp.write_text('existing compositor')
        self.apply()
        record = self.prefix / 'state/cosmic-forge/greeter-installation.json'
        before = record.read_bytes(), record.stat().st_mtime_ns
        args = SimpleNamespace(prefix=self.prefix, restore=False, binary=None, build=False, release_tag=None)
        with patch.object(install, 'installed_packages', return_value=(PACKAGES, '1.10.0')), patch.object(install, 'obtain', side_effect=AssertionError('must skip download')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                install.run(args)
        self.assertIn('already_installed', output.getvalue())
        self.assertEqual((record.read_bytes(), record.stat().st_mtime_ns), before)
        recovery.restore(self.prefix)
        self.assertFalse((self.prefix / 'bin/cosmic-greeter').exists())
        self.assertEqual(other.read_text(), 'existing Settings')
        self.assertEqual(comp.read_text(), 'existing compositor')

    def test_unowned_or_modified_launcher_is_preserved(self):
        launcher = self.prefix / 'bin/cosmic-greeter'
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
        with patch.object(install, 'installed_packages', side_effect=[(PACKAGES, '1.10.0'), ({'cosmic-greeter':'changed'}, '1.10.0')]), patch.object(install, 'obtain', return_value=self.binary):
            with self.assertRaises(RuntimeError):
                install.run(args)
        self.assertFalse((self.prefix / 'bin/cosmic-greeter').exists())

    def test_rejects_an_old_greeter_revision(self):
        with patch.object(install.subprocess, 'check_output', return_value=CONFIG['sources']['1.10.0'] + '-forge-greeter-r0'):
            with self.assertRaises(RuntimeError):
                self.apply()
        self.assertFalse((self.prefix / 'bin/cosmic-greeter').exists())

    def test_migrates_only_the_exact_veil_launcher_and_keeps_settings(self):
        legacy = self.prefix / 'state/cosmic-veil/installation.json'
        legacy.parent.mkdir(parents=True)
        launcher = self.prefix / 'bin/cosmic-greeter'
        launcher.parent.mkdir()
        settings = launcher.with_name('cosmic-settings')
        settings.write_text('Veil Settings must remain untouched')
        saved = {'bindir': str(launcher.parent), 'destination': str(self.prefix / 'veil/bin'),
                 'previous': {'cosmic-greeter': None}, 'packages': PACKAGES, 'backend': 'pacman'}
        legacy.write_text(json.dumps(saved))
        binary = Path(saved['destination']) / 'cosmic-greeter'
        check = '[ "$(pacman -Q cosmic-greeter 2>/dev/null)" = ' + shlex.quote(PACKAGES['cosmic-greeter']) + ' ]'
        original = ('#!/bin/sh\n# Managed by cosmic-veil\nif ' + check + ' && [ -x ' + shlex.quote(str(binary)) + ' ] && ' + shlex.quote(str(binary)) + ' --version >/dev/null 2>&1; then\n'
                    '    exec ' + shlex.quote(str(binary)) + ' "$@"\nfi\nexec /usr/bin/cosmic-greeter "$@"\n').encode()
        launcher.write_bytes(original)
        before = legacy.read_bytes(), settings.read_bytes()
        self.apply()
        self.assertEqual((self.prefix / 'state/cosmic-forge/greeter-previous-launcher').read_bytes(), original)
        recovery.restore(self.prefix)
        self.assertFalse(launcher.exists())
        self.assertEqual((legacy.read_bytes(), settings.read_bytes()), before)

    def test_separate_greeter_asset(self):
        self.assertEqual(asset_name('1.10.0', 'aarch64', 'greeter'), 'cosmic-greeter-1.10.0-linux-aarch64.xz')
