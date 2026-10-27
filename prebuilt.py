# SPDX-License-Identifier: GPL-3.0-only
"""Download a verified native compositor asset, without building or changing launchers."""
import json
import lzma
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

from recovery import digest, progress, xdg

REPOSITORY = 'motixo/cosmic-forge-engine'
MAX_BINARY = 128 * 1024 * 1024
MAX_ASSET = 64 * 1024 * 1024


def architecture():
    machine = platform.machine().lower()
    if machine in ('x86_64', 'amd64'):
        return 'x86_64'
    if machine in ('aarch64', 'arm64'):
        return 'aarch64'
    raise RuntimeError('No prebuilt engine binary for this architecture; build from source')


def program_name(component):
    return {'compositor': 'cosmic-comp', 'panel': 'cosmic-panel', 'greeter': 'cosmic-greeter'}[component]


def asset_name(version, machine, component='compositor'):
    return f'{program_name(component)}-{version}-linux-{machine}.xz'


def release_metadata(tag=None):
    if tag is not None and not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', tag):
        raise RuntimeError('Invalid engine release tag')
    suffix = 'tags/' + tag if tag else 'latest'
    with tempfile.NamedTemporaryFile() as temporary:
        path = Path(temporary.name)
        download(f'https://api.github.com/repos/{REPOSITORY}/releases/{suffix}', path, 2 * 1024 * 1024)
        return json.loads(path.read_bytes())


def download(url, destination, size):
    subprocess.run(['curl', '--disable', '--fail', '--silent', '--show-error', '--location',
                    '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '10',
                    '--user-agent', 'COSMIC-Forge-Engine', '--max-time', '45' if size <= 2 * 1024 * 1024 else '180', '--max-filesize', str(size), '--output', str(destination), url], check=True)


def from_release(release, version, machine, cache, fetch=download, component='compositor'):
    name = asset_name(version, machine, component)
    if not isinstance(release, dict) or not isinstance(release.get('assets'), list):
        raise RuntimeError('Invalid engine release metadata')
    asset = next((a for a in release['assets'] if isinstance(a, dict) and a.get('name') == name), None)
    if asset is None:
        raise RuntimeError('No prebuilt engine binary for this COSMIC version/architecture; build from source')
    url = asset.get('browser_download_url', '')
    checksum = asset.get('digest', '')
    size = asset.get('size')
    if not isinstance(url, str) or not url.startswith(f'https://github.com/{REPOSITORY}/releases/download/') or \
       not isinstance(checksum, str) or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', checksum) or \
       type(size) is not int or not 0 < size <= MAX_ASSET:
        raise RuntimeError('Invalid prebuilt compositor release metadata')
    expected = checksum[7:].lower()
    directory = cache / expected
    archive = directory / 'compositor.xz'
    binary = directory / program_name(component)
    if directory.is_symlink() or binary.is_symlink() or archive.is_symlink():
        raise RuntimeError('Preserved a replaced compositor cache')
    directory.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        progress('downloading_binary')
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            pending = Path(temporary.name)
        try:
            fetch(url, pending, size)
            if pending.stat().st_size != size or digest(pending.read_bytes()) != expected:
                raise RuntimeError('Prebuilt compositor checksum or size mismatch; installation preserved')
            os.replace(pending, archive)
        finally:
            pending.unlink(missing_ok=True)
    if archive.stat().st_size != size or digest(archive.read_bytes()) != expected:
        raise RuntimeError('Preserved a modified cached compositor archive')
    decoder = lzma.LZMADecompressor(memlimit=256 * 1024 * 1024)
    try:
        payload = decoder.decompress(archive.read_bytes(), max_length=MAX_BINARY + 1)
    except lzma.LZMAError as error:
        raise RuntimeError('Invalid compressed compositor asset') from error
    if not decoder.eof or decoder.unused_data or len(payload) > MAX_BINARY:
        raise RuntimeError('Invalid or oversized prebuilt compositor')
    expected_machine = {'x86_64': 62, 'aarch64': 183}[machine]
    if len(payload) < 64 or payload[:6] != b'\x7fELF\x02\x01' or int.from_bytes(payload[18:20], 'little') != expected_machine:
        raise RuntimeError('Prebuilt compositor architecture does not match this system')
    if binary.exists():
        if binary.read_bytes() != payload or not os.access(binary, os.X_OK):
            raise RuntimeError('Preserved a modified cached compositor')
        return binary
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        pending = Path(temporary.name)
        temporary.write(payload)
        temporary.flush()
        os.fsync(temporary.fileno())
    try:
        pending.chmod(0o755)
        os.replace(pending, binary)
    finally:
        pending.unlink(missing_ok=True)
    return binary


def obtain(version, config, tag=None, prefix=None, component='compositor'):
    minimum = tuple(int(part) for part in config['prebuilt']['glibc_min'].split('.'))
    libc, current = platform.libc_ver()
    if libc != 'glibc' or tuple(int(part) for part in current.split('.')) < minimum:
        raise RuntimeError('Prebuilt engine binaries require glibc ' + config['prebuilt']['glibc_min'] +
                           ' or newer; build from source')
    cache = (prefix / 'cache' if prefix else xdg('XDG_CACHE_HOME', '.cache')) / 'cosmic-forge/binaries'
    binary = from_release(release_metadata(tag), version, architecture(), cache, component=component)
    marker = {'panel': '-forge-panel-r', 'greeter': '-forge-greeter-r', 'compositor': '-forge-r'}[component]
    revision = config.get('revision', config.get('renderer_revision'))
    expected = config['sources'][version] + marker + str(revision)
    try:
        output = subprocess.check_output([str(binary), '--version'], text=True, stderr=subprocess.STDOUT, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('This prebuilt engine binary cannot run on the installed libraries; build from source') from error
    if expected not in output:
        raise RuntimeError('Prebuilt compositor revision does not match this engine package')
    return binary
