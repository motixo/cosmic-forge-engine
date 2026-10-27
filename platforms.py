# SPDX-License-Identifier: GPL-3.0-only
"""Native package queries shared by installation and compositor launch guards."""
import json
from pathlib import Path
import platform
import re
import subprocess

ROOT = Path(__file__).resolve().parent
PACKAGES = ('cosmic-comp', 'cosmic-settings', 'cosmic-panel', 'cosmic-greeter')


def package_manager():
    release = platform.freedesktop_os_release()
    identities = {release.get('ID', ''), *release.get('ID_LIKE', '').split()}
    if 'arch' in identities:
        return 'pacman'
    if 'fedora' in identities:
        return 'rpm'
    if identities & {'ubuntu', 'pop', 'debian'}:
        return 'dpkg-query'
    raise RuntimeError('Supported systems: Arch Linux, Fedora, Ubuntu and Pop!_OS')


def package_query(manager, name):
    if manager == 'pacman':
        return ['pacman', '-Q', name]
    if manager == 'rpm':
        return ['rpm', '-q', '--qf', '%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}', name]
    if manager == 'dpkg-query':
        return ['dpkg-query', '-W', '-f=${Package} ${Version} ${db:Status-Status}', name]
    raise RuntimeError('Unsupported package manager')


def compositor_version(package):
    sources = json.loads((ROOT / 'release.json').read_text())['sources']
    version_text = package.split()[1]
    match = re.match(r'^(?:[0-9]+:)?([0-9]+\.[0-9]+\.[0-9]+)(?:[-+~.]|$)', version_text)
    if match and match[1] in sources:
        return match[1]
    # Pop packages may use 0.1~timestamp~distribution~commit instead of epoch versions.
    for candidate in [version_text, None]:
        if candidate is None:
            candidate = subprocess.check_output(['/usr/bin/cosmic-comp', '--version'], text=True, timeout=15)
        hashes = re.findall(r'(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])', candidate)
        for version, commit in sources.items():
            if any(commit.startswith(value) for value in hashes):
                return version
    raise RuntimeError('No tested Forge engine for this COSMIC version. Published versions: ' + ', '.join(sources))


def installed_packages():
    manager = package_manager()
    packages = {}
    for name in PACKAGES:
        result = subprocess.run(package_query(manager, name), check=True, capture_output=True, text=True)
        package = result.stdout.strip()
        fields = package.split()
        if len(fields) < 2 or fields[0] != name or '\n' in package or '\r' in package or \
           (manager == 'dpkg-query' and (len(fields) != 3 or fields[2] != 'installed')):
            raise RuntimeError('Install the COSMIC desktop packages first: ' + name)
        packages[name] = package
    return packages, compositor_version(packages['cosmic-comp'])
