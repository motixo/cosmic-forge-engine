#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build only the patched panel; COSMIC Settings and the desktop stay untouched."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from recovery import progress


def prepare(source, version, local=None):
    config = json.loads((ROOT / 'release.json').read_text())
    pin = config['sources'][version]
    patches = [ROOT / 'penumbra.patch', ROOT / 'colors.patch']
    marker = source / '.forge-source.json'
    expected = {'commit': pin, 'revision': config['revision'],
                'patch_sha256': hashlib.sha256(b''.join(p.read_bytes() for p in patches)).hexdigest()}
    if marker.exists():
        if json.loads(marker.read_text()) != expected:
            raise RuntimeError('Panel source changed: use a fresh --work-dir')
        return
    if source.exists():
        raise RuntimeError('Preserved an existing unmarked panel source directory')
    source.parent.mkdir(parents=True, exist_ok=True)
    if local:
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(local), str(source)], check=True)
        subprocess.run(['git', 'checkout', '--detach', pin], cwd=source, check=True)
    else:
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        subprocess.run(['git', 'fetch', '--depth', '1', config['upstream'], pin], cwd=source, check=True)
        subprocess.run(['git', 'checkout', '--detach', 'FETCH_HEAD'], cwd=source, check=True)
    for patch in patches:
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=source, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=source, check=True)
    marker.write_text(json.dumps(expected, indent=2) + '\n')


def main():
    config = json.loads((ROOT / 'release.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cosmic-version', choices=list(config['sources']), required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--target-dir', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    source = (args.work_dir or ROOT.parent / 'build/panel-sources' / args.cosmic_version).resolve()
    progress('preparing')
    prepare(source, args.cosmic_version, args.source)
    if args.prepare_only:
        return
    target = (args.target_dir or ROOT.parent / 'build/panel-target' / args.cosmic_version).resolve()
    command = ['cargo', 'build', '--release', '--locked', '--bin', 'cosmic-panel', '-j', str(args.jobs)]
    if args.offline:
        command.append('--offline')
    version = config['sources'][args.cosmic_version] + '-forge-panel-r' + str(config['revision'])
    progress('building')
    subprocess.run(command, cwd=source, check=True, env=dict(os.environ,
                   CARGO_TARGET_DIR=str(target), CARGO_PROFILE_RELEASE_LTO='thin', FORGE_PANEL_VERSION=version))
    output = ROOT.parent / 'build/bin'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output, delete=False) as temporary:
        pending = Path(temporary.name)
    try:
        shutil.copy2(target / 'release/cosmic-panel', pending)
        os.replace(pending, output / 'cosmic-panel')
    finally:
        pending.unlink(missing_ok=True)
    print('Panel built: ' + str(output / 'cosmic-panel'))


if __name__ == '__main__':
    main()
