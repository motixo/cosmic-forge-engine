#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build a portable native release binary on the declared glibc baseline."""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prebuilt import architecture, asset_name, download, program_name

DISPLAY_INFO_URL = 'https://gitlab.freedesktop.org/emersion/libdisplay-info/-/archive/0.3.0/libdisplay-info-0.3.0.tar.gz'
DISPLAY_INFO_SHA256 = '2b467e3336aec63819d6aca28d7310d3dc7415b2b3a3c3a5aec9d3727053c078'


def native_library():
    work = ROOT / 'build/portable-native'
    prefix = work / 'prefix'
    if (prefix / 'lib/libdisplay-info.a').exists():
        return prefix
    work.mkdir(parents=True, exist_ok=True)
    archive = work / 'source.tar.gz'
    download(DISPLAY_INFO_URL, archive, 4 * 1024 * 1024)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != DISPLAY_INFO_SHA256:
        raise RuntimeError('libdisplay-info source checksum mismatch')
    with tarfile.open(archive) as source:
        source.extractall(work, filter='data')
    source = work / 'libdisplay-info-0.3.0'
    build = work / 'meson'
    if not build.exists():
        subprocess.run(['meson', 'setup', str(build), str(source), '--prefix', str(prefix),
                        '--libdir', 'lib', '--default-library', 'static', '--buildtype', 'release'], check=True)
    subprocess.run(['meson', 'compile', '-C', str(build)], check=True)
    subprocess.run(['meson', 'install', '-C', str(build)], check=True)
    return prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['compositor', 'panel', 'greeter'], default='compositor')
    parser.add_argument('--cosmic-version', required=True)
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    component_root = ROOT if args.component == 'compositor' else ROOT / args.component
    config = json.loads((component_root / 'release.json').read_text())
    if args.cosmic_version not in config['sources']:
        parser.error('COSMIC version is not pinned in release.json')
    prefix = native_library()
    environment = dict(os.environ, PKG_CONFIG_PATH=str(prefix / 'lib/pkgconfig'),
                       SYSTEM_DEPS_LIBDISPLAY_INFO_LINK='static')
    suffix = Path(args.cosmic_version) if args.component == 'compositor' else Path(args.component) / args.cosmic_version
    subprocess.run([sys.executable, str(component_root / 'build.py'), '--cosmic-version', args.cosmic_version,
                    '--work-dir', str(ROOT / 'build/portable-sources' / suffix),
                    '--target-dir', str(ROOT / 'build/portable-target' / suffix),
                    '--jobs', str(args.jobs)], env=environment, check=True)
    binary = ROOT / 'build/bin' / program_name(args.component)
    subprocess.run(['strip', '--strip-unneeded', str(binary)], check=True)
    subprocess.run([str(binary), '--version'], check=True)
    linked = subprocess.check_output(['ldd', str(binary)], text=True)
    if 'not found' in linked or 'libdisplay-info.so' in linked:
        raise RuntimeError('The portable binary has unresolved or unbundled display-info dependencies')
    symbols = subprocess.check_output(['readelf', '--version-info', str(binary)], text=True)
    requirements = [tuple(map(int, value.split('.'))) for value in re.findall(r'GLIBC_([0-9.]+)', symbols)]
    if max(requirements) > tuple(map(int, config['prebuilt']['glibc_min'].split('.'))):
        raise RuntimeError('Build this binary on the declared glibc baseline or an older system')
    output = ROOT / 'build/release'
    output.mkdir(parents=True, exist_ok=True)
    with binary.open('rb') as source, lzma.open(output / asset_name(args.cosmic_version, architecture(), args.component), 'wb', preset=6) as target:
        shutil.copyfileobj(source, target)
    print('Portable asset: ' + str(output / asset_name(args.cosmic_version, architecture(), args.component)))


if __name__ == '__main__':
    main()
