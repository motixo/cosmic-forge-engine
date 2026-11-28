#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Package the pinned engine sources for COSMIC Forge's compositor installer."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {'build.py', 'install.py', 'recovery.py', 'release.json'}


def package(root):
    release = json.loads((root / 'release.json').read_text())
    if type(release.get('abi')) is not int or release['abi'] < 1 or \
       type(release.get('renderer_revision')) is not int or release['renderer_revision'] < 1:
        raise ValueError('ABI and renderer revision must be positive integers')
    sources = release.get('sources')
    if not isinstance(sources, dict) or not sources or any(
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-pop\.[0-9a-f]{7})?', name)
            or not isinstance(pin, str) or not re.fullmatch(r'[0-9a-f]{40}', pin)
            for name, pin in sources.items()):
        raise ValueError('Invalid pinned compositor builds')
    toolchains = release.get('toolchains')
    if not isinstance(toolchains, dict) or set(toolchains) != set(sources) or any(
            not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value)
            for value in toolchains.values()):
        raise ValueError('Every compositor build must have a pinned Rust toolchain')
    names = release['bundle_files']
    if not isinstance(names, list) or not 1 <= len(names) <= 128 or \
       any(not isinstance(name, str) or len(name) > 200 or
           any(part in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9._-]+', part)
               for part in name.split('/')) for name in names):
        raise ValueError('Invalid engine file list')
    if len(set(names)) != len(names) or not REQUIRED.issubset(names):
        raise ValueError('Missing or duplicate engine files')
    series = release.get('patch_series')
    if not isinstance(series, dict) or set(series) != {'cosmic-comp', 'smithay'}:
        raise ValueError('Invalid patch targets')
    patches = []
    for entries in series.values():
        if not isinstance(entries, list):
            raise ValueError('Invalid patch series')
        for entry in entries:
            if not isinstance(entry, dict) or not {'path'} <= entry.keys() <= {'path', 'versions'}:
                raise ValueError('Invalid patch entry')
            name = entry['path']
            versions = entry.get('versions')
            if not isinstance(name, str) or not name.startswith('patches/') or not name.endswith('.patch'):
                raise ValueError('Invalid patch path')
            if 'versions' in entry and (
                not isinstance(versions, list) or not versions
                or any(not isinstance(version, str) or version not in sources for version in versions)
                or len(set(versions)) != len(versions)
            ):
                raise ValueError('Invalid patch versions')
            patches.append(name)
    if len(set(patches)) != len(patches) or set(patches) != {name for name in names if name.endswith('.patch')}:
        raise ValueError('Each bundled patch must appear in exactly one series')
    overlays = release.get('overlays')
    if not isinstance(overlays, dict) or not overlays or any(
        name not in names or not isinstance(dest, str)
        or not re.fullmatch(r'src/backend/render/(?:wayland/forge_[a-z_]+\.rs|shaders/forge_[a-z_]+\.frag)', dest)
        for name, dest in overlays.items()
    ) or len(set(overlays.values())) != len(overlays):
        raise ValueError('Invalid renderer overlays')
    files = {}
    for name in names:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Engine source must be inside the repository')
        files[name] = path.read_text(encoding='utf-8')
    data = (json.dumps({'format': 1, 'abi': release['abi'], 'files': files},
                       ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('Engine package exceeds 2 MiB')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/release/cosmic-forge-engine.json')
    args = parser.parse_args()
    data = package(ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(str(args.output))


if __name__ == '__main__':
    main()
