#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline restoration of the original COSMIC compositor; preserve settings and recovery data."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from login_path import restore_changes

SYSTEM_COMPOSITOR = Path('/usr/bin/cosmic-comp')


def xdg(name, fallback):
    path = Path(os.environ.get(name, Path.home() / fallback))
    return path if path.is_absolute() else Path.home() / fallback


def paths(prefix=None):
    data = prefix / 'share' if prefix else xdg('XDG_DATA_HOME', '.local/share')
    state = prefix / 'state' if prefix else xdg('XDG_STATE_HOME', '.local/state')
    launcher = (prefix or Path.home() / '.local') / 'bin/cosmic-comp'
    return data / 'cosmic-forge/engine', state, launcher


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


def apply_changes(changes, writer=atomic, verify=None):
    previous = {}
    for path in changes:
        if path.is_symlink():
            raise RuntimeError(f'Preserved a replaced setup file: {path}')
        previous[path] = (path.read_bytes(), path.stat().st_mode & 0o7777) if path.exists() else (None, 0o600)
    completed = []
    try:
        for path, (data, mode) in changes.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                writer(path, data, mode)
            completed.append(path)
        if verify is not None:
            verify()
    except (OSError, RuntimeError):
        for path in reversed(completed):
            data, mode = previous[path]
            if data is None:
                path.unlink(missing_ok=True)
            else:
                writer(path, data, mode)
        raise


def record_for(state, launcher):
    path = state / 'cosmic-forge/engine-installation.json'
    if path.is_symlink():
        raise RuntimeError('Preserved a replaced engine installation record')
    record = json.loads(path.read_text()) if path.exists() else None
    if path.exists() and (not isinstance(record, dict) or not record):
        raise RuntimeError('Invalid engine installation record')
    if launcher.is_symlink():
        raise RuntimeError('Preserved a compositor launcher symlink')
    current = launcher.read_bytes() if launcher.exists() else None
    if record:
        if record.get('abi') != 1 or record.get('launcher') != str(launcher) or current is None or digest(current) != record.get('launcher_sha256'):
            raise RuntimeError('The compositor launcher changed outside Forge; preserved it')
        previous = record.get('previous')
        if not isinstance(previous, dict) or 'data' not in previous or not isinstance(previous.get('mode'), int) or not 0 <= previous['mode'] <= 0o777 or (previous.get('data') is not None and not isinstance(previous['data'], str)):
            raise RuntimeError('Invalid compositor recovery metadata')
        if previous['data'] is not None:
            base64.b64decode(previous['data'], validate=True)
    elif current is not None:
        legacy = state / 'cosmic-crystal/installation.json'
        previous = json.loads(legacy.read_text()) if legacy.exists() else {}
        expected = previous.get('shims', {}).get('cosmic-comp')
        if expected is None or current != expected.encode():
            raise RuntimeError('Preserved a compositor launcher owned by another installation')
    return path, record, current


def restore(prefix=None):
    progress('restoring')
    _, state, launcher = paths(prefix)
    record_file, record, current = record_for(state, launcher)
    if not SYSTEM_COMPOSITOR.is_file() or not os.access(SYSTEM_COMPOSITOR, os.X_OK):
        raise RuntimeError('The original COSMIC compositor is unavailable; reinstall the cosmic-comp system package first')
    with SYSTEM_COMPOSITOR.open('rb') as binary:
        if binary.read(4) != b'\x7fELF':
            raise RuntimeError('Expected the original native COSMIC system compositor; preserved the current launcher')
    changes = restore_changes(launcher, (record or {}).get('login_profiles', []))
    if current is not None:
        changes[launcher] = (None, 0o755)
    if record:
        changes[record_file] = (None, 0o600)
    apply_changes(changes)
    print('Original COSMIC compositor restored. Log out and back in to activate it.', flush=True)
    progress('complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path)
    args = parser.parse_args()
    prefix = args.prefix.resolve() if args.prefix else None
    progress('checking')
    _, state, _ = paths(prefix)
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'cosmic-forge-engine.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        restore(prefix)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit(str(error))
