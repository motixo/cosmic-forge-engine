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
    names = release['bundle_files']
    if not isinstance(names, list) or not 1 <= len(names) <= 128 or \
       any(not isinstance(name, str) or len(name) > 200 or
           any(part in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9._-]+', part)
               for part in name.split('/')) for name in names):
        raise ValueError('Invalid engine file list')
    if len(set(names)) != len(names) or not REQUIRED.issubset(names):
        raise ValueError('Missing or duplicate engine files')
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
