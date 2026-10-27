#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline restoration of the original COSMIC greeter; preserve settings and recovery data."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SYSTEM_GREETER = Path('/usr/bin/cosmic-greeter')


def xdg(name, fallback):
    path = Path(os.environ.get(name, Path.home() / fallback))
    return path if path.is_absolute() else Path.home() / fallback


def paths(prefix=None):
    data = prefix / 'share' if prefix else xdg('XDG_DATA_HOME', '.local/share')
    state = prefix / 'state' if prefix else xdg('XDG_STATE_HOME', '.local/state')
    launcher = (prefix or Path.home() / '.local') / 'bin/cosmic-greeter'
    return data / 'cosmic-forge/greeter', state, launcher


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def progress(stage):
    print('FORGE_PROGRESS:' + stage, flush=True)


def record_for(state, launcher):
    path = state / 'cosmic-forge/greeter-installation.json'
    if path.is_symlink():
        raise RuntimeError('Preserved a replaced engine installation record')
    record = json.loads(path.read_text()) if path.exists() else None
    if path.exists() and (not isinstance(record, dict) or not record):
        raise RuntimeError('Invalid engine installation record')
    if launcher.is_symlink():
        raise RuntimeError('Preserved a greeter launcher symlink')
    current = launcher.read_bytes() if launcher.exists() else None
    if record:
        if record.get('abi') != 1 or record.get('launcher') != str(launcher) or current is None or digest(current) != record.get('launcher_sha256'):
            raise RuntimeError('The greeter launcher changed outside Forge; preserved it')
        previous = record.get('previous')
        if not isinstance(previous, dict) or 'data' not in previous or not isinstance(previous.get('mode'), int) or not 0 <= previous['mode'] <= 0o777 or (previous.get('data') is not None and not isinstance(previous['data'], str)):
            raise RuntimeError('Invalid greeter recovery metadata')
        if previous['data'] is not None:
            base64.b64decode(previous['data'], validate=True)
    elif current is not None:
        # Migrate only a launcher exactly reproduced from Veil's installation receipt.
        import shlex
        from platforms import package_query
        owned = False
        for name in ('cosmic-veil', 'cosmic-lockscreen-wallpaper'):
            legacy = state / name / 'installation.json'
            if not legacy.is_file() or legacy.is_symlink():
                continue
            saved = json.loads(legacy.read_text())
            if saved.get('bindir') != str(launcher.parent):
                continue
            packages = saved.get('packages', {})
            checks = ' && '.join('[ "$(' + shlex.join(package_query(saved.get('backend', 'pacman'), package)) +
                                 ' 2>/dev/null)" = ' + shlex.quote(version) + ' ]'
                                 for package, version in sorted(packages.items()))
            binary = Path(saved['destination']) / 'cosmic-greeter'
            fallback = saved['previous']['cosmic-greeter'] or '/usr/bin/cosmic-greeter'
            for marker in ('# Managed by cosmic-veil\n', '# Managed by cosmic-lockscreen-wallpaper\n'):
                expected = ('#!/bin/sh\n' + marker +
                    'if ' + checks + ' && [ -x ' + shlex.quote(str(binary)) + ' ] && ' + shlex.quote(str(binary)) + ' --version >/dev/null 2>&1; then\n'
                    '    exec ' + shlex.quote(str(binary)) + ' "$@"\nfi\n'
                    'exec ' + shlex.quote(str(fallback)) + ' "$@"\n').encode()
                owned |= current == expected
        if not owned:
            raise RuntimeError('Preserved a greeter launcher owned by another installation')
    return path, record, current


def restore(prefix=None):
    progress('restoring')
    _, state, launcher = paths(prefix)
    record_file, record, current = record_for(state, launcher)
    current_mode = launcher.stat().st_mode & 0o777 if current is not None else 0o755
    if not SYSTEM_GREETER.is_file() or not os.access(SYSTEM_GREETER, os.X_OK):
        raise RuntimeError('The original COSMIC greeter is unavailable; reinstall the cosmic-greeter system package first')
    with SYSTEM_GREETER.open('rb') as binary:
        if binary.read(4) != b'\x7fELF':
            raise RuntimeError('Expected the original native COSMIC system greeter; preserved the current launcher')
    try:
        if current is not None:
            launcher.unlink()
        if record:
            record_file.unlink()
    except OSError:
        if current is not None:
            atomic(launcher, current, current_mode)
        raise
    print('Original COSMIC greeter restored. Sign out and back in to activate it.', flush=True)
    progress('complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path)
    args = parser.parse_args()
    prefix = args.prefix.resolve() if args.prefix else None
    progress('checking')
    _, state, _ = paths(prefix)
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'cosmic-forge-greeter.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        restore(prefix)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit(str(error))
