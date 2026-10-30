# SPDX-License-Identifier: GPL-3.0-only
import base64
import json
import os
from pathlib import Path
import shutil
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import login_path
from recovery import apply_changes
from test_install import installer, PACKAGES, VERSION


class LoginPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forge user's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.launcher = self.root / 'bin/cosmic-comp'
        self.launcher.parent.mkdir()
        self.launcher.write_text('#!/bin/sh\nprintf FORGE\n')
        self.launcher.chmod(0o755)
        self.state = self.root / 'state'

    def prepare(self, shell, record=None, environment=None):
        return login_path.prepare(self.state, self.launcher, record, self.home,
                                  shutil.which(shell), environment or {})

    def test_clean_login_repairs_zsh_even_when_terminal_path_already_finds_forge(self):
        if not shutil.which('zsh'):
            self.skipTest('Zsh is not installed')
        profile = self.home / '.zprofile'
        original = b'# user configuration without final newline'
        profile.write_bytes(original)
        profile.chmod(0o640)
        with patch.dict(os.environ, {'PATH': str(self.launcher.parent) + ':' + os.defpath}):
            changes, profiles, verify = self.prepare('zsh')
        self.assertIn(profile, changes)
        apply_changes(changes, verify=verify)
        _, path, _ = login_path.inspect_login(shutil.which('zsh'), self.home, {})
        self.assertTrue(login_path.launcher_first(path, self.launcher, self.home))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in changes}
        again, saved, verify_again = self.prepare('zsh', {'login_profiles': profiles})
        self.assertEqual((again, saved, verify_again), ({}, profiles, None))
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in changes}, before)
        backups = list((self.state / 'cosmic-forge/login-profile-backups').glob('*.json'))
        self.assertEqual(base64.b64decode(json.loads(backups[0].read_text())['original']), original)
        apply_changes(login_path.restore_changes(self.launcher, profiles))
        self.assertEqual(profile.read_bytes(), original)
        self.assertEqual(profile.stat().st_mode & 0o777, 0o640)
        self.assertTrue(backups[0].exists())

    def test_restore_preserves_user_edits_outside_the_owned_block(self):
        changes, profiles, verify = self.prepare('bash')
        apply_changes(changes, verify=verify)
        profile = self.home / '.profile'
        profile.write_bytes(b'# added before\n' + profile.read_bytes() + b'# added after\n')
        apply_changes(login_path.restore_changes(self.launcher, profiles))
        self.assertEqual(profile.read_bytes(), b'# added before\n# added after\n')

    def test_posix_login_shell_profile(self):
        for name in ('sh', 'dash'):
            if not shutil.which(name):
                continue
            with self.subTest(shell=name):
                changes, profiles, verify = self.prepare(name)
                apply_changes(changes, verify=verify)
                apply_changes(login_path.restore_changes(self.launcher, profiles))
                self.assertFalse((self.home / '.profile').exists())

    def test_bash_uses_first_existing_login_profile_and_preserves_existing_setup(self):
        for name in ('.profile', '.bash_login', '.bash_profile'):
            (self.home / name).write_text('# custom login settings\n')
        changes, profiles, verify = self.prepare('bash')
        self.assertEqual(Path(profiles[0]['path']), self.home / '.bash_profile')
        apply_changes(changes, verify=verify)
        self.assertEqual((self.home / '.profile').read_text(), '# custom login settings\n')
        apply_changes(login_path.restore_changes(self.launcher, profiles))
        profile = self.home / '.bash_profile'
        profile.write_text('export PATH=' + shlex.quote(str(self.launcher.parent)) + ':"$PATH"\n')
        self.assertEqual(self.prepare('bash'), ({}, [], None))

    def test_failed_post_install_check_rolls_back_all_files(self):
        if not shutil.which('zsh'):
            self.skipTest('Zsh is not installed')
        # .zlogin runs after .zprofile and can override the repaired path.
        (self.home / '.zlogin').write_text('export PATH=/usr/bin:/bin\n')
        changes, _, verify = self.prepare('zsh')
        record = self.state / 'record.json'
        record.parent.mkdir(parents=True)
        record.write_bytes(b'original record')
        launcher_before = self.launcher.read_bytes()
        changes[self.launcher] = (b'new launcher', 0o755)
        changes[record] = (b'new record', 0o600)
        with self.assertRaisesRegex(RuntimeError, 'previous installation was restored'):
            apply_changes(changes, verify=verify)
        self.assertFalse((self.home / '.zprofile').exists())
        self.assertEqual((self.home / '.zlogin').read_text(), 'export PATH=/usr/bin:/bin\n')
        self.assertEqual(record.read_bytes(), b'original record')
        self.assertEqual(self.launcher.read_bytes(), launcher_before)

    def test_profile_and_launcher_roll_back_if_the_installation_record_cannot_be_written(self):
        from recovery import atomic
        changes, _, verify = self.prepare('bash')
        record = self.state / 'record.json'
        original = self.launcher.read_bytes()
        changes[self.launcher] = (b'new launcher', 0o755)
        changes[record] = (b'new record', 0o600)
        def fail_record(path, data, mode):
            if path == record:
                raise OSError('simulated record write failure')
            atomic(path, data, mode)
        with self.assertRaises(OSError):
            apply_changes(changes, writer=fail_record, verify=verify)
        self.assertFalse((self.home / '.profile').exists())
        self.assertEqual(self.launcher.read_bytes(), original)
        self.assertFalse(record.exists())

    def test_custom_zdotdir_and_created_profile_cleanup(self):
        if not shutil.which('zsh'):
            self.skipTest('Zsh is not installed')
        dotdir = self.home / 'custom shell'
        dotdir.mkdir()
        (self.home / '.zshenv').write_text('export ZDOTDIR="' + str(dotdir) + '"\n')
        changes, profiles, verify = self.prepare('zsh')
        self.assertEqual(Path(profiles[0]['path']), dotdir / '.zprofile')
        apply_changes(changes, verify=verify)
        apply_changes(login_path.restore_changes(self.launcher, profiles))
        self.assertFalse((dotdir / '.zprofile').exists())

    def test_symlinks_modified_blocks_and_invalid_recovery_are_preserved(self):
        profile = self.home / '.profile'
        target = self.root / 'dotfile'
        target.write_text('# owned by the user\n')
        profile.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            self.prepare('bash')
        self.assertEqual(target.read_text(), '# owned by the user\n')
        profile.unlink()
        changes, profiles, verify = self.prepare('bash')
        apply_changes(changes, verify=verify)
        profile.write_bytes(profile.read_bytes().replace(b'export PATH=', b'export MY_PATH='))
        with self.assertRaisesRegex(RuntimeError, 'modified Forge PATH block'):
            login_path.restore_changes(self.launcher, profiles)
        for invalid in (None, 'invalid', [{}], [dict(profiles[0], path='/tmp/other-file')]):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                login_path.restore_changes(self.launcher, invalid)

    def test_already_installed_engine_repairs_login_without_rebuilding_or_rewriting_binary(self):
        # Migrate an installation created before login PATH repair existed.
        self.launcher.unlink()
        binary = self.root / 'input-engine'
        binary.write_bytes(b'\x7fELFcosmic-forge-engine-v1')
        with patch.object(installer, 'package_manager', return_value='pacman'), \
             patch.object(installer.subprocess, 'check_output', return_value=VERSION), \
             patch.object(installer, 'prepare_login_path', return_value=({}, [], None)):
            installer.install(binary, PACKAGES, '1.10.0', self.root)
        record_path = self.state / 'cosmic-forge/engine-installation.json'
        record = json.loads(record_path.read_text())
        installed = Path(record['destination']) / 'cosmic-comp'
        preserved = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (installed, self.launcher)}
        # Use Bash explicitly; the machine's account shell must not affect this isolated test.
        def bash_prepare(*args, **kwargs):
            return login_path.prepare(*args, shell=shutil.which('bash'), **kwargs)
        with patch.object(sys, 'argv', ['install.py', '--prefix', str(self.root)]), \
             patch.object(installer, 'installed_packages', return_value=(PACKAGES, '1.10.0')), \
             patch.object(installer.subprocess, 'check_output', return_value=VERSION), \
             patch.object(installer, 'prepare_login_path', side_effect=bash_prepare), \
             patch.object(installer, 'obtain', side_effect=AssertionError('must not download')):
            installer.main()
        repaired = json.loads(record_path.read_text())
        self.assertTrue(repaired['login_profiles'])
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in preserved}, preserved)
        system = self.root / 'stock-engine'
        system.write_bytes(b'\x7fELFstock')
        system.chmod(0o755)
        with patch('recovery.SYSTEM_COMPOSITOR', system):
            installer.restore(self.root)
        self.assertFalse((self.home / '.profile').exists())
        self.assertFalse(self.launcher.exists())

    def test_fish_uses_config_snippet_and_never_universal_variables(self):
        managed = login_path.block('fish', self.launcher.parent)
        self.assertIn(b'fish_add_path --path --move --prepend', managed)
        self.assertNotIn(b'--universal', managed)
        with self.assertRaisesRegex(RuntimeError, 'Automatic login PATH setup'):
            self.prepare('python3')

    @unittest.skipUnless(shutil.which('fish'), 'Fish is not installed')
    def test_real_fish_login_with_custom_config_directory(self):
        config = self.home / 'custom fish config'
        changes, profiles, verify = self.prepare('fish', environment={'XDG_CONFIG_HOME': str(config)})
        profile = config / 'fish/conf.d/90-cosmic-forge.fish'
        self.assertEqual(Path(profiles[0]['path']), profile)
        apply_changes(changes, verify=verify)
        apply_changes(login_path.restore_changes(self.launcher, profiles))
        self.assertFalse(profile.exists())
