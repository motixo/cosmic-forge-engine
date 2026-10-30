# SPDX-License-Identifier: GPL-3.0-only
"""Managed login-shell PATH setup shared with offline recovery."""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import stat
import subprocess

BEGIN = '# >>> COSMIC Forge login PATH >>>'
END = '# <<< COSMIC Forge login PATH <<<'
MARKER = b'\0COSMIC_FORGE_LOGIN_PATH\0'


def block(kind, directory):
    if kind == 'fish':
        quoted = "'" + str(directory).replace('\\', '\\\\').replace("'", "\\'") + "'"
        body = f'if status is-login\n    fish_add_path --path --move --prepend {quoted}\nend'
    elif kind in ('bash', 'zsh', 'sh', 'dash'):
        quoted = shlex.quote(str(directory))
        body = f'if [ "${{PATH%%:*}}" != {quoted} ]; then\n    export PATH={quoted}:"$PATH"\nfi'
    else:
        raise RuntimeError('Automatic login PATH setup supports Bash, Zsh, Fish and POSIX sh. '
                           'Add the Forge launcher directory to your login shell PATH manually.')
    return (BEGIN + '\n' + body + '\n' + END + '\n').encode()


def read_profile(path):
    if path.is_symlink():
        raise RuntimeError(f'Preserved login profile symlink: {path}. Add the Forge launcher directory manually.')
    if not path.exists():
        return None, 0o600
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RuntimeError(f'Preserved login profile not owned by this user: {path}')
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode)


def inspect_login(shell, home, environment=None):
    # An interactive terminal's PATH can hide the missing directory in the real desktop session.
    account = pwd.getpwuid(os.getuid())
    source = os.environ if environment is None else environment
    env = {key: source[key] for key in ('ZDOTDIR', 'XDG_CONFIG_HOME') if source.get(key)}
    env.update(HOME=str(home), USER=account.pw_name, LOGNAME=account.pw_name,
               SHELL=shell, PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', LANG='C.UTF-8',
               XDG_DATA_HOME=str(home / '.local/share'), XDG_STATE_HOME=str(home / '.local/state'),
               XDG_CACHE_HOME=str(home / '.cache'))
    kind = Path(shell).name
    block(kind, home / '.local/bin')  # Reject unsupported syntax before running or editing a profile.
    if kind == 'fish':
        command = "printf '\\0COSMIC_FORGE_LOGIN_PATH\\0%s\\0%s\\0%s\\0' (string join : -- $PATH) \"$HOME\" \"$__fish_config_dir\""
    else:
        command = "printf '\\0COSMIC_FORGE_LOGIN_PATH\\0%s\\0%s\\0%s\\0' \"$PATH\" \"${ZDOTDIR:-$HOME}\" \"${XDG_CONFIG_HOME:-$HOME/.config}\""
    try:
        result = subprocess.run([shell, '-lc', command], cwd=home, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('Could not check the login shell PATH; the previous installation was preserved.') from error
    values = result.stdout.rsplit(MARKER, 1)[-1].split(b'\0')
    if result.returncode or MARKER not in result.stdout or len(values) != 4:
        raise RuntimeError('The login shell did not report its PATH. Check its startup files before installing Forge.')
    path, zdotdir, config = map(os.fsdecode, values[:3])
    if kind == 'zsh':
        profile = Path(zdotdir) / '.zprofile'
    elif kind == 'fish':
        profile = Path(config) / 'conf.d/90-cosmic-forge.fish'
    elif kind == 'bash':
        profile = next((home / name for name in ('.bash_profile', '.bash_login', '.profile')
                        if (home / name).exists() or (home / name).is_symlink()), home / '.profile')
    else:
        profile = home / '.profile'
    if not profile.is_absolute():
        profile = home / profile
    return kind, path, Path(os.path.abspath(profile))


def launcher_first(path, launcher, home):
    for directory in path.split(os.pathsep):
        directory = Path(directory) if directory else home
        if not directory.is_absolute():
            directory = home / directory
        candidate = directory / 'cosmic-comp'
        # The parent may be ready before a fresh installation creates the launcher itself.
        if candidate.resolve() == launcher.resolve():
            return True
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return False
    return False


def restore_changes(launcher, profiles):
    if not isinstance(profiles, list) or len(profiles) > 16:
        raise RuntimeError('Invalid login profile recovery record')
    changes = {}
    seen = set()
    for entry in profiles:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise RuntimeError('Invalid login profile recovery entry')
        path = Path(entry['path'])
        if (not path.is_absolute() or '..' in path.parts or path in seen
                or path.name not in ('.profile', '.bash_profile', '.bash_login', '.zprofile', '90-cosmic-forge.fish')
                or type(entry.get('created')) is not bool or type(entry.get('separator')) is not bool
                or not isinstance(entry.get('installed_sha256'), str)):
            raise RuntimeError('Invalid login profile recovery path')
        seen.add(path)
        managed = block(entry.get('kind'), launcher.parent)
        current, mode = read_profile(path)
        if current is None:
            continue
        if managed not in current:
            if BEGIN.encode() in current or END.encode() in current:
                raise RuntimeError(f'Preserved a modified Forge PATH block in {path}. Remove it manually before retrying.')
            continue  # The user already removed our block.
        if current.count(managed) != 1:
            raise RuntimeError(f'Preserved duplicate Forge PATH blocks in {path}')
        cleaned = current.replace(managed, b'', 1)
        if hashlib.sha256(current).hexdigest() == entry['installed_sha256'] and entry['separator']:
            cleaned = cleaned[:-1]
        changes[path] = (None if entry['created'] and not cleaned else cleaned, mode)
    return changes


def prepare(state, launcher, record, home=None, shell=None, environment=None):
    home = Path.home() if home is None else home
    shell = pwd.getpwuid(os.getuid()).pw_shell if shell is None else shell
    profiles = (record or {}).get('login_profiles', [])
    restore_changes(launcher, profiles)  # Validate existing ownership without applying restoration.
    profiles = list(profiles)
    kind, path, profile = inspect_login(shell, home, environment)
    if launcher_first(path, launcher, home):
        return {}, profiles, None
    before, mode = read_profile(profile)
    managed = block(kind, launcher.parent)
    if BEGIN.encode() in (before or b'') or END.encode() in (before or b''):
        raise RuntimeError(f'The Forge PATH block in {profile} is overridden by another startup file. '
                           'Keep the Forge launcher directory first in the login PATH.')
    separator = bool(before and not before.endswith(b'\n'))
    after = (before or b'') + (b'\n' if separator else b'') + managed
    entry = {'path': str(profile), 'kind': kind, 'created': before is None,
             'separator': separator, 'installed_sha256': hashlib.sha256(after).hexdigest()}
    backup = (json.dumps(dict(entry, original=base64.b64encode(before).decode() if before is not None else None,
                              mode=mode), sort_keys=True, indent=2) + '\n').encode()
    backup_path = state / 'cosmic-forge/login-profile-backups' / (hashlib.sha256(backup).hexdigest() + '.json')
    if backup_path.parent.is_symlink() or backup_path.is_symlink():
        raise RuntimeError('Preserved a replaced login profile backup path')
    if backup_path.exists() and backup_path.read_bytes() != backup:
        raise RuntimeError('Preserved a modified login profile backup')
    changes = {backup_path: (backup, 0o600), profile: (after, mode)}
    profiles = [saved for saved in profiles if saved['path'] != str(profile)] + [entry]
    if len(profiles) > 16:
        raise RuntimeError('Too many managed login profiles; restore the original compositor first')

    def verify():
        _, updated, _ = inspect_login(shell, home, environment)
        if not launcher_first(updated, launcher, home):
            raise RuntimeError(f'{profile} did not make the Forge launcher first in the login PATH. '
                               'The previous installation was restored; check later startup files.')

    return changes, profiles, verify
