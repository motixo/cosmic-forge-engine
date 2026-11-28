#!/usr/bin/env python3
"""Patch the exact Smithay dependency from the compositor lockfile."""
import os
from pathlib import Path
import re
import shutil
import subprocess

URL = 'https://github.com/smithay/smithay.git'


def prepare_smithay(source, offline, vendor, patches):
    lockfile = source / 'Cargo.lock'
    lock = lockfile.read_text()
    lines = re.findall(r'^source = "git\+https://github\.com/smithay/smithay\.git\?rev=[^#"]+#([a-f0-9]{40})"\n',
                       lock, flags=re.MULTILINE)
    if len(lines) != 1:
        raise RuntimeError('Could not resolve the pinned Smithay source')
    revision, = lines
    checkout = source / '.forge-smithay'
    if vendor:
        crates = [file.parent for file in vendor.rglob('Cargo.toml')
                  if re.search(r'^name = "smithay"$', file.read_text(), re.MULTILINE)]
        if len(crates) != 1:
            raise RuntimeError('Could not identify Smithay in the pinned Cargo vendor directory')
        shutil.copytree(crates[0], checkout)
        for path in checkout.rglob('*'):
            path.chmod(path.stat().st_mode | 0o200)
    else:
        cargo_home = Path(os.environ.get('CARGO_HOME') or Path.home() / '.cargo')
        cached = next((db for db in (cargo_home / 'git/db').glob('smithay-*')
                       if subprocess.run(['git', '-C', str(db), 'cat-file', '-e', revision + '^{commit}'],
                                         capture_output=True).returncode == 0), None)
        if cached:
            subprocess.run(['git', 'clone', '--shared', '--no-checkout', str(cached), str(checkout)], check=True)
        elif offline:
            raise RuntimeError('The pinned Smithay revision is absent from the offline Cargo cache')
        else:
            subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
            subprocess.run(['git', 'fetch', '--depth', '1', URL, revision], cwd=checkout, check=True)
        subprocess.run(['git', 'checkout', '--detach', revision], cwd=checkout, check=True)

    for patch in patches:
        if vendor:
            subprocess.run(['patch', '-p1', '--batch', '-i', str(patch)], cwd=checkout, check=True)
        else:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=checkout, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=checkout, check=True)
    manifest = source / 'Cargo.toml'
    updated, count = re.subn(r'^smithay = \{ git = "https://github\.com/smithay/smithay\.git", rev = "[^"]+" \}$',
                             'smithay = { path = ".forge-smithay" }', manifest.read_text(), flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError('Could not replace the pinned Smithay manifest entry')
    manifest.write_text(updated)
    lockfile.write_text(re.sub(r'^source = "git\+https://github\.com/smithay/smithay\.git\?rev=[^\n]+\n',
                               '', lock, flags=re.MULTILINE))
