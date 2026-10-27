# SPDX-License-Identifier: GPL-3.0-only
"""Regression tests for the real artwork decoder; opt in with FORGE_MEDIA_BINARY."""
import os
from pathlib import Path
import struct
import subprocess
import unittest
import zlib

BINARY = os.environ.get("FORGE_MEDIA_BINARY")


def png(width, height, pixels):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@unittest.skipUnless(BINARY, "Set FORGE_MEDIA_BINARY to the newly built cosmic-greeter")
class ArtworkDecoderTests(unittest.TestCase):
    def decode(self, data):
        result = subprocess.run([str(Path(BINARY).resolve()), "--veil-decode-art"],
                                input=data, capture_output=True, timeout=8,
                                env={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/no-media-test-bus",
                                     "WAYLAND_DISPLAY": "no-media-test-display"})
        return result

    def test_valid_png_emits_only_fixed_size_rgba_without_a_desktop(self):
        result = self.decode(png(1, 1, b"\x00\x18\xbe\xaa"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"\x18\xbe\xaa\xff" * (128 * 128))

    def test_corrupt_images_and_active_document_formats_are_rejected(self):
        for data in (b"", b"broken PNG", b"\x89PNG\r\n\x1a\n",
                     b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'):
            with self.subTest(data=data[:20]):
                result = self.decode(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")

    def test_dimension_limit_rejects_small_compressed_bombs(self):
        result = self.decode(png(100000, 100000, b"\x00"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_input_larger_than_eight_megabytes_is_rejected(self):
        result = self.decode(b"x" * (8 * 1024 * 1024 + 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
