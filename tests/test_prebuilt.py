# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import json
import lzma
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prebuilt


def payload(machine=62):
    data = bytearray(64)
    data[:6] = b'\x7fELF\x02\x01'
    data[18:20] = machine.to_bytes(2, 'little')
    return bytes(data) + b'cosmic-forge-engine-v1'


def release(data):
    name = prebuilt.asset_name('1.10.0', 'x86_64')
    return {'assets': [{'name': name, 'size': len(data),
                        'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
                        'browser_download_url': f'https://github.com/{prebuilt.REPOSITORY}/releases/download/engine-v1/{name}'}]}


class PrebuiltTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / 'cache'
        self.bytes = payload()
        self.compressed = lzma.compress(self.bytes)
        self.release = release(self.compressed)

    def fetch(self, _url, destination, _size):
        destination.write_bytes(self.compressed)

    def test_verified_binary_is_decompressed_and_reused_without_downloading(self):
        binary = prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, self.fetch)
        self.assertEqual(binary.read_bytes(), self.bytes)
        self.assertTrue(binary.stat().st_mode & 0o111)
        before = binary.stat().st_mtime_ns
        def forbidden(*_):
            self.fail('Identical cached binary was downloaded again')
        self.assertEqual(prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, forbidden), binary)
        self.assertEqual(binary.stat().st_mtime_ns, before)

    def test_wrong_repository_digest_size_or_missing_asset_never_downloads(self):
        for key, value in [('browser_download_url', 'https://github.com/other/engine/releases/download/v1/file'),
                           ('digest', None), ('digest', 'sha256:bad'), ('size', 0), ('size', prebuilt.MAX_ASSET + 1)]:
            metadata = json.loads(json.dumps(self.release))
            metadata['assets'][0][key] = value
            with self.assertRaises(RuntimeError):
                prebuilt.from_release(metadata, '1.10.0', 'x86_64', self.cache, lambda *_: self.fail('Must not download'))
        for metadata in [{'assets': []}, [], {'assets': 'bad'}]:
            with self.assertRaises(RuntimeError):
                prebuilt.from_release(metadata, '1.10.0', 'x86_64', self.cache, self.fetch)
        self.assertFalse(self.cache.exists())

    def test_corruption_wrong_architecture_and_oversized_output_are_rejected(self):
        def corrupted(_url, destination, _size):
            destination.write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError):
            prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, corrupted)
        wrong = lzma.compress(payload(183))
        with self.assertRaises(RuntimeError):
            prebuilt.from_release(release(wrong), '1.10.0', 'x86_64', self.cache, lambda _, p, __: p.write_bytes(wrong))
        with patch.object(prebuilt, 'MAX_BINARY', 10):
            with self.assertRaises(RuntimeError):
                prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, self.fetch)
        self.assertFalse(any(self.cache.rglob('cosmic-comp')))

    def test_modified_cache_and_symlinks_are_preserved(self):
        binary = prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, self.fetch)
        binary.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, self.fetch)
        self.assertEqual(binary.read_bytes(), b'changed')
        binary.unlink()
        outside = self.cache.parent / 'outside'
        outside.write_bytes(b'preserved')
        binary.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            prebuilt.from_release(self.release, '1.10.0', 'x86_64', self.cache, self.fetch)
        self.assertEqual(outside.read_bytes(), b'preserved')

    def test_glibc_baseline_is_checked_before_any_download(self):
        config = json.loads((ROOT / 'release.json').read_text())
        for libc in [('glibc', '2.34'), ('musl', '1.2.5')]:
            with patch.object(prebuilt.platform, 'libc_ver', return_value=libc), \
                 patch.object(prebuilt, 'release_metadata', side_effect=AssertionError('Must not download')):
                with self.assertRaises(RuntimeError):
                    prebuilt.obtain('1.10.0', config)

    def test_prebuilt_revision_is_checked_before_installation(self):
        config = json.loads((ROOT / 'release.json').read_text())
        expected = config['sources']['1.10.0'] + '-forge-r' + str(config['renderer_revision'])
        with patch.object(prebuilt.platform, 'libc_ver', return_value=('glibc', '2.35')), \
             patch.object(prebuilt, 'release_metadata', return_value=self.release), \
             patch.object(prebuilt, 'from_release', return_value=Path('/private/fixture/cosmic-comp')):
            with patch.object(prebuilt.subprocess, 'check_output', return_value=f'cosmic-comp 1.0.0 (git commit {expected})'):
                self.assertEqual(prebuilt.obtain('1.10.0', config), Path('/private/fixture/cosmic-comp'))
            for revision in ['wrong renderer', expected + '0', expected + '-dirty']:
                with self.subTest(revision=revision), patch.object(prebuilt.subprocess, 'check_output', return_value=revision):
                    with self.assertRaises(RuntimeError):
                        prebuilt.obtain('1.10.0', config)
