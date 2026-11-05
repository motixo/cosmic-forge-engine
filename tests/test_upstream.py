# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_upstream', ROOT / 'scripts/check_upstream.py')
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)
PIN = 'ec674cd7bc02af3b3445458a3eef2951728bd719'
INDEX = f'''Package: cosmic-comp
Version: 0.1~1793802426~24.04~{PIN[:7]}
Filename: pool/noble/cosmic-comp/{PIN}/cosmic-comp.deb

Package: cosmic-session
Version: 1.11.0~1792525615~24.04~4b0a0a2

'''


class UpstreamTests(unittest.TestCase):
    def config(self):
        return json.loads((ROOT / 'release.json').read_text())

    def test_current_official_builds_do_not_open_a_duplicate_pr(self):
        self.assertEqual(upstream.pending_sources(self.config(), INDEX, []), {})

    def test_pop_snapshot_is_detected_without_changing_existing_epoch_pins(self):
        config = self.config()
        del config['sources']['1.11.0-pop.ec674cd']
        before = dict(config['sources'])
        self.assertEqual(upstream.pending_sources(config, INDEX, []), {'1.11.0-pop.ec674cd': PIN})
        self.assertEqual(config['sources'], before)

    def test_new_official_tag_and_pop_snapshot_can_be_checked_together(self):
        pin = '1234567' + 'a' * 33
        tags = [{'name': 'epoch-1.12.0', 'commit': {'sha': pin}},
                {'name': 'epoch-1.9.0', 'commit': {'sha': 'b' * 40}}]
        self.assertEqual(upstream.pending_sources(self.config(), INDEX, tags), {'1.12.0': pin})

    def test_package_paths_versions_and_duplicate_entries_are_validated(self):
        for invalid in (INDEX.replace(PIN, 'bad-pin'), INDEX.replace(PIN[:7], 'abcdef0', 1),
                        INDEX + INDEX, INDEX.replace('1.11.0~1792525615', 'unknown~1792525615')):
            with self.subTest(index=invalid), self.assertRaises((ValueError, KeyError)):
                upstream.pending_sources(self.config(), invalid, [])

    def test_snapshot_identifier_collision_cannot_replace_a_source(self):
        config = self.config()
        config['sources']['1.11.0-pop.ec674cd'] = 'ec674cd' + '0' * 33
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            upstream.pending_sources(config, INDEX, [])

    def test_toolchains_are_read_from_the_exact_commit_and_pinned(self):
        with patch.object(upstream, 'fetch', return_value=b'[toolchain]\nchannel="1.95"\n') as fetch:
            self.assertEqual(upstream.toolchain(PIN), '1.95.0')
            self.assertIn(PIN, fetch.call_args.args[0])
        with patch.object(upstream, 'fetch', return_value=b'[toolchain]\nchannel="stable"\n'):
            with self.assertRaisesRegex(ValueError, 'pinned'):
                upstream.toolchain(PIN)
