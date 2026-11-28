# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build


class BuildTests(unittest.TestCase):
    def test_output_replaces_symlink_without_overwriting_referent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            (target / 'release').mkdir(parents=True)
            (target / 'release/cosmic-comp').write_bytes(b'new binary')
            referent = root / 'existing'
            referent.write_bytes(b'leave this file alone')
            output = root / 'cosmic-comp'
            output.symlink_to(referent)
            argv = ['build.py', '--cosmic-version', '1.12.0', '--target-dir', str(target),
                    '--output', str(output)]
            with patch.object(sys, 'argv', argv), patch.object(build, 'prepare'), \
                 patch.object(build.subprocess, 'run'):
                build.main()
            self.assertEqual(referent.read_bytes(), b'leave this file alone')
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_bytes(), b'new binary')
