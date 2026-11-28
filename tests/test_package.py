# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
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
        self.assertFalse(any(name.startswith(('panel/', 'greeter/')) for name in bundle['files']))
        for name, source in bundle['files'].items():
            self.assertEqual(source, (ROOT / name).read_text())
        self.assertEqual(publisher.package(ROOT), data)

    def test_release_matrix_can_select_one_pinned_build(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        script = textwrap.dedent(workflow.split(
            "python - <<'PYTHON' >> \"$GITHUB_OUTPUT\"\n", 1
        )[1].split('          PYTHON', 1)[0])
        source_count = len(json.loads((ROOT / 'release.json').read_text())['sources'])
        for selected, expected in [('', 2 * source_count), ('1.11.0-pop.6968c5d', 2)]:
            with self.subTest(selected=selected):
                result = subprocess.run(
                    [sys.executable, '-c', script], cwd=ROOT,
                    env=dict(os.environ, COSMIC_VERSION=selected),
                    check=True, capture_output=True, text=True,
                )
                matrix = json.loads(result.stdout.removeprefix('value='))['include']
                self.assertEqual(len(matrix), expected)
                if selected:
                    self.assertEqual({entry['version'] for entry in matrix}, {selected})
        invalid = subprocess.run(
            [sys.executable, '-c', script], cwd=ROOT,
            env=dict(os.environ, COSMIC_VERSION='unknown'),
            capture_output=True, text=True,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('COSMIC version is not pinned', invalid.stderr)

    def test_invalid_manifest_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = json.loads((ROOT / 'release.json').read_text())
            for name in release['bundle_files']:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, root / name)
            invalid = [dict(release, abi=0), dict(release, renderer_revision=True),
                       dict(release, sources={'../escape': 'a' * 40}),
                       dict(release, sources={'1.11.0': 'not-a-commit'}),
                       dict(release, toolchains={}),
                       dict(release, toolchains=dict.fromkeys(release['sources'], 'stable')),
                       dict(release, bundle_files=['../escape']),
                       dict(release, bundle_files=['/tmp/escape']),
                       dict(release, bundle_files=release['bundle_files'] + ['install.py']),
                       dict(release, bundle_files=['install.py']),
                       dict(release, patch_series={'cosmic-comp': []}),
                       dict(release, patch_series={
                           **release['patch_series'],
                           'smithay': [{'path': 'patches/smithay-damage.patch', 'versions': ['unknown']}],
                       }),
                       dict(release, overlays={'runtime.rs': '../escape.rs'})]
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
