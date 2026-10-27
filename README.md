# COSMIC Forge Engine

The glass compositor engine for [COSMIC Forge](https://github.com/motixo/cosmic-forge),
a mod manager for COSMIC. This repository owns the pinned COSMIC patches, shader
runtime, builder and installer. [Glass shaders](https://github.com/motixo/cosmic-forge-shaders)
have their own repository. No replacement COSMIC Settings is installed.

Forge Settings downloads the latest stable `cosmic-forge-engine.json` release,
checks its SHA-256 digest and ABI, then runs the installer. Compatible engine fixes
and renderer updates need no new app release. Installation currently supports
Arch Linux and derivatives with matching COSMIC 1.10.0 or 1.11.0 packages.

## Build and test

Requires Python 3.11+, Git, Rust and COSMIC compositor development libraries.

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/package_engine.py
python build.py --cosmic-version 1.10.0
# Or 1.11.0. The executable is build/bin/cosmic-comp.
python install.py --binary "$PWD/build/bin/cosmic-comp"
python recovery.py
```

The builder downloads upstream commits pinned in `release.json`. Use `--source`,
`--target-dir` and `--offline` to reuse a local checkout and Cargo cache.
`--prepare-only` checks patches without compiling. Existing unmarked work folders
are preserved. Build output stays under `build/`.

Install keeps immutable versioned binaries and the previous launcher for recovery.
An exact match of renderer revision, COSMIC packages, launcher and binary checksum
skips rebuilding and reinstalling. Restore removes the verified Forge or recorded
legacy Crystal launcher so the next login uses the original `/usr/bin/cosmic-comp`.
It preserves preferences, packs, old binaries, recovery data and COSMIC Settings.
Unknown or modified launchers are preserved. System files are never overwritten,
and the running session is never restarted. Sign out and back in to activate.
`install.py --restore` remains supported; Forge also embeds the standalone recovery
helper so restoring the stock compositor works offline.

## Publishing

`release.json` lists the UTF-8 sources included in the release bundle. Add helper
files there when needed. The format is `{ "format": 1, "abi": 1, "files": { "path":
"source text" } }`, limited to 2 MiB. Paths must be relative and contain only
letters, digits, dots, underscores and hyphens, with no empty or dot segments.
Forge requires `install.py`, `build.py`, `recovery.py` and `release.json`.

A stable `engine-v*` tag runs tests and publishes `cosmic-forge-engine.json`.
Increment `renderer_revision` when changing compositor patches or runtime code;
installer-only fixes keep the existing revision to avoid needless rebuilds.
Keep ABI 1 compatible with installed Forge apps. Breaking the runtime/bundle
contract requires a new ABI and a matching app release. Helpers emit
`FORGE_PROGRESS:<stage>` for the app's progress bar.

## Isolated rendering checks

The retained fixtures use private XDG directories, Xvfb and D-Bus. They require
Pillow, python-xlib, Cairo, Wayland scanner and cosmic-bg. They do not change host
preferences.

```sh
python build_test_surface.py
python preview.py --headless --effect aero
# In another terminal:
CRYSTAL_QA_DIR="$PWD/build/preview" python tests/verify_aero.py
FORGE_SHADERS_DIR=/path/cosmic-forge-shaders \
  CRYSTAL_QA_DIR="$PWD/build/preview" python tests/verify_packs.py
touch build/preview/stop
```

The patches retain the original COSMIC Crystal optics, Aero, native Frosted Glass
integration and upstream notices. GPL-3.0-only; upstream dependencies retain their
own licenses.
