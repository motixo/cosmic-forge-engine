#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check that the built compositor opens a private Wayland socket under Xvfb."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compositor', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='forge-smoke-') as directory:
        root = Path(directory)
        env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_DATA_HOME=str(root / 'data'), XDG_CACHE_HOME=str(root / 'cache'),
                   XDG_RUNTIME_DIR=directory, XDG_CURRENT_DESKTOP='COSMIC',
                   XDG_SESSION_TYPE='wayland', COSMIC_BACKEND='winit',
                   WINIT_UNIX_BACKEND='x11', LIBGL_ALWAYS_SOFTWARE='1')
        env.pop('WAYLAND_DISPLAY', None)
        parent, child = socket.socketpair()
        with parent, child, subprocess.Popen(
            [str(args.compositor.resolve()), '--no-xwayland'],
            env=dict(env, COSMIC_SESSION_SOCK=str(child.fileno())),
            pass_fds=(child.fileno(),),
        ) as compositor:
            child.close()
            parent.settimeout(30)
            try:
                with parent.makefile('rb') as session:
                    size, = struct.unpack('=H', session.read(2))
                    variables = dict(json.loads(session.read(size))['variables'])
                display = variables['WAYLAND_DISPLAY']
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(5)
                    client.connect(str(root / display))
                if compositor.poll() is not None:
                    raise RuntimeError('The compositor stopped during startup')
                print('COSMIC accepted a connection in the isolated Wayland session.')
            finally:
                if compositor.poll() is None:
                    compositor.terminate()
                    try:
                        compositor.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        compositor.kill()
                        compositor.wait()


if __name__ == '__main__':
    main()
