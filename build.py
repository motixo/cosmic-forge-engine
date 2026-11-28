#!/usr/bin/env python3
"""Build the glass renderer separately; never install or restart the desktop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from recovery import progress
from prepare_smithay import prepare_smithay

ROOT = Path(__file__).resolve().parent


def selected_patches(release, target, version):
    return [
        ROOT / entry['path'] for entry in release['patch_series'][target]
        if 'versions' not in entry or version in entry['versions']
    ]


def source_inputs(version, release):
    pin = release['sources'][version]
    patches = selected_patches(release, 'cosmic-comp', version)
    smithay = selected_patches(release, 'smithay', version)
    files = [ROOT / name for name in ('build.py', 'prepare_smithay.py', *release['overlays'])]

    def fingerprint(paths):
        return [[p.relative_to(ROOT).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()]
                for p in paths]

    return patches, {
        'commit': pin,
        'binary_version': pin + '-forge-r' + str(release['renderer_revision']),
        'toolchain': release['toolchains'][version],
        'cosmic_patches': fingerprint(patches),
        'smithay_patches': fingerprint(smithay),
        'overlay_destinations': release['overlays'],
        'sources': fingerprint(files),
    }


def apply_recipe(source, version, release, recipe, offline=False, vendor=None):
    # An unpacked source archive can sit inside an unrelated Git worktree.
    environment = dict(os.environ, GIT_CEILING_DIRECTORIES=str(source.resolve().parent))
    for patch in selected_patches(release, 'cosmic-comp', version):
        subprocess.run(['git', 'apply', str(patch)], cwd=source, env=environment, check=True)
    prepare_smithay(source, offline, vendor, selected_patches(release, 'smithay', version))
    for name, destination in release['overlays'].items():
        shutil.copy2(ROOT / name, source / destination)
    binary_version = recipe['binary_version']
    (source / 'build.rs').write_text(f'fn main() {{ println!("cargo:rustc-env=GIT_HASH={binary_version}"); }}\n')


def prepare(source, version, local=None, offline=False):
    release = json.loads((ROOT / 'release.json').read_text())
    _, expected = source_inputs(version, release)
    pin = expected['commit']
    marker = source / '.forge-source.json'
    if marker.exists():
        if json.loads(marker.read_text()) != expected:
            raise RuntimeError('Renderer source changed: use a fresh --work-dir')
        return
    if source.exists():
        raise RuntimeError('Refusing to modify an existing unmarked source directory')
    if offline and not local:
        raise RuntimeError('An upstream checkout is required for offline preparation')
    source.parent.mkdir(parents=True, exist_ok=True)
    if local:
        subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(local), str(source)], check=True)
        subprocess.run(['git', 'checkout', '--detach', pin], cwd=source, check=True)
    else:
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        subprocess.run(['git', 'fetch', '--depth', '1', release['upstream'], pin], cwd=source, check=True)
        subprocess.run(['git', 'checkout', '--detach', 'FETCH_HEAD'], cwd=source, check=True)
    apply_recipe(source, version, release, expected, offline)
    marker.write_text(json.dumps(expected, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cosmic-version', choices=list(json.loads((ROOT / 'release.json').read_text())['sources']), required=True)
    parser.add_argument('--source', type=Path, help='Use an existing upstream Git checkout as a local clone source')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--target-dir', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--prepared-source', type=Path, help='Apply the recipe to a source tree supplied by Nix')
    parser.add_argument('--smithay-vendor', type=Path, help='Use the existing Cargo vendor tree')
    parser.add_argument('--output', type=Path, help='Destination for the built compositor')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if args.prepared_source or args.smithay_vendor:
        if not args.prepared_source or not args.smithay_vendor or not args.prepare_only:
            parser.error('--prepared-source and --smithay-vendor require each other and --prepare-only')
        release = json.loads((ROOT / 'release.json').read_text())
        _, recipe = source_inputs(args.cosmic_version, release)
        apply_recipe(args.prepared_source, args.cosmic_version, release, recipe,
                     offline=True, vendor=args.smithay_vendor.resolve())
        return
    work = (args.work_dir or ROOT / 'build/engine-sources' / args.cosmic_version).resolve()
    progress('preparing')
    prepare(work, args.cosmic_version, args.source, args.offline)
    if args.prepare_only:
        return
    target = (args.target_dir or ROOT / 'build/engine-target' / args.cosmic_version).resolve()
    command = ['cargo', 'build', '--release', '--locked', '--bin', 'cosmic-comp', '-j', str(args.jobs)]
    if args.offline:
        command.append('--offline')
    progress('building')
    subprocess.run(command, cwd=work, env=dict(os.environ, CARGO_TARGET_DIR=str(target),
                   CARGO_PROFILE_RELEASE_LTO='thin'), check=True)
    output = args.output or ROOT / 'build/bin/cosmic-comp'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temporary:
        pending = Path(temporary.name)
    try:
        shutil.copy2(target / 'release/cosmic-comp', pending)
        os.replace(pending, output)
    finally:
        pending.unlink(missing_ok=True)
    print('Renderer built: ' + str(output))


if __name__ == '__main__':
    main()
