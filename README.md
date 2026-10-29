# COSMIC Forge Engine

The shader compositor engine for [COSMIC Forge](https://github.com/motixo/cosmic-forge).
Includes the glass patches, custom shader runtime, builder and installer.
[Shaders](https://github.com/motixo/cosmic-forge-shaders) live in a separate repository.

Supports Arch Linux, Fedora, Ubuntu and Pop!_OS. Prebuilt binaries cover pinned
COSMIC 1.10/1.11 on x86_64 and ARM64 with glibc 2.35+.

## Install

Use **Settings → Patches** in Forge, or run:

```sh
python3 install.py            # Install the matching prebuilt compositor
python3 install.py --build    # Build and install from pinned source
python3 recovery.py           # Restore the original system compositor
```

Sign out and back in to activate. Matching installations are reused. Restore
preserves preferences, shader packs, previous binaries, recovery data and COSMIC
Settings. Modified or unowned launchers are preserved; system files are never replaced.
Forge embeds the recovery helper so Restore also works offline.

## Development

Requires Python 3.11+, Git, Rust and COSMIC compositor development libraries.

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/package_engine.py
python3 build.py --cosmic-version 1.10.0
```

Upstream commits are pinned in `release.json`. Use `--prepare-only` to check patches
without compiling, or `--source`, `--target-dir` and `--offline` to reuse local
sources and caches. The output is `build/bin/cosmic-comp`.

## Publishing

An `engine-v*` tag runs tests and builds compositor binaries for each pinned COSMIC
version on x86_64 and ARM64 before publishing `cosmic-forge-engine.json` and `.xz`
assets. Manual workflow runs can update a selected release tag.

The source bundle uses ABI 1 and is limited to 2 MiB. Forge verifies repository,
size, SHA-256 and ABI before running the installer. Prebuilt binaries are checked
for architecture, native dependencies and upstream/renderer revision. Install
never starts a source build automatically.

Keep ABI 1 compatible with Forge. Increment `renderer_revision` when compositor
patches or runtime code change; installer-only changes retain the revision.

## Rendering checks

These checks use private XDG directories, Xvfb and D-Bus. They require Pillow,
python-xlib, Cairo, Wayland scanner and cosmic-bg.

```sh
python3 build_test_surface.py
python3 preview.py --headless --effect aero
# In another terminal:
CRYSTAL_QA_DIR="$PWD/build/preview" python3 tests/verify_aero.py
CRYSTAL_QA_DIR="$PWD/build/preview" python3 tests/verify_generic.py
touch build/preview/stop
```

GPL-3.0-only. Upstream notices are retained in the patches. The static
libdisplay-info dependency retains its MIT notice in `LICENSE.libdisplay-info`.
