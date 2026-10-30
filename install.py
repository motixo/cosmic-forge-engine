#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build/install the user's glass renderer, or restore the original system compositor."""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from recovery import apply_changes, atomic, digest, paths, progress, record_for, restore, xdg
from login_path import prepare as prepare_login_path
from platforms import PACKAGES, installed_packages, package_manager, package_query
from prebuilt import obtain, verify_revision

ROOT = Path(__file__).resolve().parent


def already_installed(record, packages, version, prefix=None, candidate_digest=None):
    if not record or record.get('cosmic_version') != version or record.get('packages') != packages:
        return False
    config = json.loads((ROOT / 'release.json').read_text())
    # The first Forge installer predates this field and installed renderer revision 1.
    if record.get('renderer_revision', 1) != config['renderer_revision']:
        return False
    data, _, _ = paths(prefix)
    destination = Path(record.get('destination', ''))
    if destination.parent != data / 'versions' or not re.fullmatch(r'[0-9a-f]{16}', destination.name):
        raise RuntimeError('Invalid installed renderer path')
    binary = destination / 'cosmic-comp'
    if not binary.exists():
        return False
    if binary.is_symlink() or digest(binary.read_bytes()) != record.get('binary_sha256') or not os.access(binary, os.X_OK):
        raise RuntimeError('The installed renderer changed outside Forge; preserved it')
    try:
        verify_revision(binary, version, config)
    except RuntimeError:
        return False
    return candidate_digest is None or candidate_digest == record['binary_sha256']


def login_setup(state, launcher, record, prefix):
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if prefix is not None:
        home = prefix / 'home'
        home.mkdir(parents=True, exist_ok=True)
        return prepare_login_path(state, launcher, record, home=home, environment={})
    return prepare_login_path(state, launcher, record)


def report_already_installed(prefix=None):
    _, state, launcher = paths(prefix)
    record_file, record, _ = record_for(state, launcher)
    changes, profiles, verify = login_setup(state, launcher, record, prefix)
    if profiles != record.get('login_profiles', []):
        changes[record_file] = ((json.dumps(dict(record, login_profiles=profiles), indent=2) + '\n').encode(), 0o600)
    apply_changes(changes, atomic, verify)
    progress('complete' if verify else 'already_installed')
    print('Login PATH repaired. Log out and back in to activate Forge.' if verify else
          'The matching Forge glass renderer is already installed. No rebuild or reinstall needed.', flush=True)


def install(binary, packages, version, prefix=None):
    data, state, launcher = paths(prefix)
    if any(c in str(launcher) + str(data) + str(state) for c in '\n\r\x00'):
        raise RuntimeError('Installation paths cannot contain line breaks or NUL')
    record_file, record, current = record_for(state, launcher)
    current_mode = launcher.stat().st_mode & 0o777 if current is not None else 0o755
    payload = binary.read_bytes()
    if payload[:4] != b'\x7fELF' or b'cosmic-forge-engine-v1' not in payload:
        raise RuntimeError('Expected the native Forge ABI 1 renderer')
    config = json.loads((ROOT / 'release.json').read_text())
    verify_revision(binary, version, config)
    if already_installed(record, packages, version, prefix, digest(payload)):
        report_already_installed(prefix)
        return
    progress('installing')
    previous = record['previous'] if record else {
        'data': base64.b64encode(current).decode() if current is not None else None,
        'mode': current_mode,
    }
    fallback = state / 'cosmic-forge/engine-previous-compositor'
    if previous['data'] is not None:
        original = base64.b64decode(previous['data'], validate=True)
        if record and (not fallback.exists() or fallback.is_symlink() or fallback.read_bytes() != original):
            raise RuntimeError('Previous compositor recovery file changed; preserved it')
        if not record:
            if fallback.is_symlink() or (fallback.exists() and fallback.read_bytes() != original):
                raise RuntimeError('Preserved an existing compositor recovery file')
            if not fallback.exists():
                atomic(fallback, original, previous['mode'])
    destination = data / 'versions' / digest(payload)[:16]
    executable = destination / 'cosmic-comp'
    if executable.is_symlink() or (executable.exists() and digest(executable.read_bytes()) != digest(payload)):
        raise RuntimeError('Preserved a modified renderer binary')
    if not executable.exists():
        atomic(executable, payload, 0o755)
    quote = shlex.quote
    guard = ' && '.join('[ "$(' + shlex.join(package_query(package_manager(), name)) + ' 2>/dev/null)" = ' + quote(value) + ' ]'
                        for name, value in packages.items())
    text = ('#!/bin/sh\n# Managed by cosmic-forge renderer\n'
            'if [ "${COSMIC_FORGE_DISABLE:-0}" != 1 ] && ' + guard +
            ' && [ -x ' + quote(str(executable)) + ' ]; then\n'
            '    exec ' + quote(str(executable)) + ' "$@"\nfi\n'
            'exec ' + quote(str(fallback) if previous['data'] is not None else '/usr/bin/cosmic-comp') + ' "$@"\n')
    updated = {'abi': 1, 'renderer_revision': config['renderer_revision'],
               'cosmic_version': version, 'destination': str(destination),
               'launcher': str(launcher), 'launcher_sha256': digest(text.encode()),
               'binary_sha256': digest(payload), 'packages': packages, 'package_manager': package_manager(), 'previous': previous}
    changes, profiles, verify = login_setup(state, launcher, record, prefix)
    if profiles:
        updated['login_profiles'] = profiles
    changes[launcher] = (text.encode(), 0o755)
    changes[record_file] = ((json.dumps(updated, indent=2) + '\n').encode(), 0o600)
    apply_changes(changes, atomic, verify)
    print('Glass renderer installed. Log out and back in to activate it.', flush=True)
    progress('complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--restore', action='store_true')
    action.add_argument('--build', action='store_true', help='Build from pinned source instead of downloading a binary')
    action.add_argument('--binary', type=Path, help='Install an already built matching ABI 1 renderer')
    parser.add_argument('--release-tag', help='Use binary assets from the same release as the engine package')
    parser.add_argument('--prefix', type=Path, help='Private installation prefix for testing')
    args = parser.parse_args()
    if args.prefix:
        args.prefix = args.prefix.resolve()
    progress('checking')
    _, state, _ = paths(args.prefix)
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'cosmic-forge-engine.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.restore:
            restore(args.prefix)
            return
        packages, version = installed_packages()
        # Check launcher ownership before starting a potentially long build.
        _, _, launcher = paths(args.prefix)
        _, record, _ = record_for(state, launcher)
        if args.binary is None and record and isinstance(record.get('packages'), dict):
            compatible = dict(record, packages={name: record['packages'].get(name) for name in PACKAGES})
            if already_installed(compatible, packages, version, args.prefix):
                if record['packages'] == packages:
                    report_already_installed(args.prefix)
                else:
                    install(Path(record['destination']) / 'cosmic-comp', packages, version, args.prefix)
                return
        binary = args.binary
        if binary is None and args.build:
            work = ROOT / 'build/sources' / version
            if work.exists() and not (work / '.forge-source.json').exists():
                work.rename(work.with_name(version + '-incomplete-' + str(time.time_ns())))
            command = ['python3', str(ROOT / 'build.py'), '--cosmic-version', version,
                       '--work-dir', str(work)]
            target = os.environ.get('COSMIC_FORGE_ENGINE_TARGET') or str(
                (args.prefix / 'cache' if args.prefix else xdg('XDG_CACHE_HOME', '.cache')) / 'cosmic-forge/engine-target' / version)
            command.extend(['--target-dir', target])
            subprocess.run(command, check=True)
            binary = ROOT / 'build/bin/cosmic-comp'
        if binary is None:
            binary = obtain(version, json.loads((ROOT / 'release.json').read_text()), args.release_tag, args.prefix)
        current_packages, current_version = installed_packages()
        if (packages, version) != (current_packages, current_version):
            raise RuntimeError('COSMIC packages changed during the build; run installation again')
        install(binary.resolve(), packages, version, args.prefix)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
