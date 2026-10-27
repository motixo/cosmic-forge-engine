# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_engine', ROOT / 'scripts/package_engine.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PackageTests(unittest.TestCase):
    def test_package_contains_exact_manifest_sources(self):
        data = publisher.package(ROOT)
        bundle = json.loads(data)
        release = json.loads((ROOT / 'release.json').read_text())
        self.assertEqual((bundle['format'], bundle['abi']), (1, release['abi']))
        self.assertEqual(set(bundle['files']), set(release['bundle_files']))
        for name, source in bundle['files'].items():
            self.assertEqual(source, (ROOT / name).read_text())
        self.assertEqual(publisher.package(ROOT), data)

    def test_invalid_manifest_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = json.loads((ROOT / 'release.json').read_text())
            for name in release['bundle_files']:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, root / name)
            invalid = [dict(release, abi=0), dict(release, renderer_revision=True),
                       dict(release, bundle_files=['../escape']),
                       dict(release, bundle_files=['/tmp/escape']),
                       dict(release, bundle_files=release['bundle_files'] + ['install.py']),
                       dict(release, bundle_files=['install.py'])]
            for manifest in invalid:
                with self.subTest(manifest=manifest):
                    (root / 'release.json').write_text(json.dumps(manifest))
                    with self.assertRaises(ValueError):
                        publisher.package(root)
            (root / 'release.json').write_text(json.dumps(release))
            (root / 'install.py').unlink()
            (root / 'install.py').symlink_to(ROOT / 'install.py')
            with self.assertRaises(ValueError):
                publisher.package(root)
