#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare reviewable source pins for new official COSMIC and Pop!_OS builds."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = 'https://apt.pop-os.org/release/dists/noble/main/binary-amd64/Packages.gz'
TAGS = 'https://api.github.com/repos/pop-os/cosmic-comp/tags?per_page=100'


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'COSMIC-Forge-Engine'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Upstream metadata exceeds 4 MiB')
    return data


def pending_sources(config, package_text, tags):
    sources = config['sources']
    current = max(tuple(map(int, name.split('.'))) for name in sources
                  if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', name))
    pending = {}
    for tag in tags:
        match = re.fullmatch(r'epoch-([0-9]+\.[0-9]+\.[0-9]+)', tag['name'])
        if match and tuple(map(int, match[1].split('.'))) > current:
            pin = tag['commit']['sha']
            if not re.fullmatch(r'[0-9a-f]{40}', pin):
                raise ValueError('Invalid upstream commit')
            pending[match[1]] = pin
    packages = {}
    for block in package_text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines()
                      if line and not line[0].isspace() and ': ' in line)
        if fields.get('Package') in ('cosmic-comp', 'cosmic-session'):
            if fields['Package'] in packages:
                raise ValueError('Ambiguous Pop!_OS package index')
            packages[fields['Package']] = fields
    compositor = packages['cosmic-comp']
    path = re.fullmatch(r'pool/noble/cosmic-comp/([0-9a-f]{40})/[^/]+\.deb', compositor['Filename'])
    suffix = re.search(r'~([0-9a-f]{7,40})$', compositor['Version'])
    epoch = re.match(r'^([0-9]+\.[0-9]+\.[0-9]+)(?:~|$)', packages['cosmic-session']['Version'])
    if not path or not suffix or not epoch or not path[1].startswith(suffix[1]):
        raise ValueError('Unrecognized Pop!_OS compositor package metadata')
    if path[1] not in {*sources.values(), *pending.values()}:
        name = epoch[1] + '-pop.' + path[1][:7]
        if name in sources:
            raise ValueError('Pop!_OS build identifier conflicts with an existing source pin')
        pending[name] = path[1]
    return pending


def toolchain(commit):
    url = f'https://raw.githubusercontent.com/pop-os/cosmic-comp/{commit}/rust-toolchain.toml'
    channel = tomllib.loads(fetch(url).decode())['toolchain']['channel']
    if not isinstance(channel, str) or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', channel):
        raise ValueError('Upstream must specify a pinned stable Rust toolchain')
    return channel + '.0' if channel.count('.') == 1 else channel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Update release.json for a compatibility PR')
    args = parser.parse_args()
    path = ROOT / 'release.json'
    config = json.loads(path.read_text())
    with gzip.GzipFile(fileobj=io.BytesIO(fetch(PACKAGES))) as stream:
        package_text = stream.read(12 * 1024 * 1024 + 1)
    if len(package_text) > 12 * 1024 * 1024:
        raise ValueError('Pop!_OS package index exceeds 12 MiB')
    pending = pending_sources(config, package_text.decode(), json.loads(fetch(TAGS)))
    print(json.dumps(pending, indent=2))
    if args.write and pending:
        for name, pin in pending.items():
            config['sources'][name] = pin
            config['toolchains'][name] = toolchain(pin)
        path.write_text(json.dumps(config, indent=2) + '\n')
        body = 'Adds compositor builds detected in the official COSMIC and Pop!_OS repositories.\n\n'
        body += '\n'.join(f'- `{name}`: `{pin[:12]}`' for name, pin in pending.items())
        body += '\n\nCompatibility builds must pass before merging.\n'
        (ROOT / 'build').mkdir(exist_ok=True)
        (ROOT / 'build/upstream-pr.md').write_text(body)
    if destination := os.environ.get('GITHUB_OUTPUT'):
        branch = 'compatibility/' + hashlib.sha256(json.dumps(pending, sort_keys=True).encode()).hexdigest()[:12]
        with open(destination, 'a') as stream:
            stream.write(f'changed={str(bool(pending)).lower()}\nbranch={branch}\n')


if __name__ == '__main__':
    main()
