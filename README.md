# COSMIC Forge Engine

The compositor and panel engine for [COSMIC Forge](https://github.com/motixo/cosmic-forge),
a mod manager for COSMIC. This repository owns the pinned COSMIC patches, shader
runtime, builders and installers. Penumbra and independent light/dark panel colors live in `panel/`. [Glass shaders](https://github.com/motixo/cosmic-forge-shaders)
have their own repository. No replacement COSMIC Settings is installed.

Forge Settings downloads the latest stable `cosmic-forge-engine.json` release,
checks its SHA-256 digest and ABI, then runs the installer.
**Install compositor** downloads a compressed, verified prebuilt executable;
**Build compositor** explicitly builds pinned source. Neither action silently
switches to the other method. Compatible engine fixes
and renderer updates need no new app release. Installation supports Arch Linux, Fedora, Ubuntu, Pop!_OS and derivatives.
Published upstream pins currently cover COSMIC 1.10.0 and 1.11.0. New tested
versions from 1.10 onward are added to `release.json` without app updates.
Unpublished versions are refused. RPM/DPKG/Arch package versions are captured
exactly for the launcher guards. Pop snapshot versions can resolve their pinned
commit from package metadata or the original system compositor.

## Build and test

Requires Python 3.11+, Git, Rust and COSMIC compositor development libraries.

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/package_engine.py
python build.py --cosmic-version 1.10.0
# Or 1.11.0. The executable is build/bin/cosmic-comp.
python install.py                 # Install the matching prebuilt release
python install.py --build         # Build and install from pinned source
python install.py --binary "$PWD/build/bin/cosmic-comp"
python recovery.py
# Independent panel component (Penumbra + theme-aware colors):
python install.py --component panel
python install.py --component panel --build
python install.py --component panel --restore
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

A stable `engine-v*` tag runs tests, builds native x86_64 and ARM64 binaries for
every pinned COSMIC version, and publishes them with `cosmic-forge-engine.json`.
The release waits for every build to pass. `workflow_dispatch` can test the full
build matrix before tagging. Binaries are built on Ubuntu 22.04 with Rust 1.93.0
and baseline glibc 2.35. libdisplay-info 0.3.0 is checksum-pinned and statically
linked; other runtime libraries use the installed desktop system. The publisher
checks ELF dependencies, symbol requirements and `--version`. XZ compression
reduces download size. The installer checks repository, SHA-256, download size,
decompression limits, architecture and embedded upstream/renderer revision before
changing launchers. Missing assets or incompatible libraries suggest **Build**.

Prebuilt assets: `cosmic-comp-<version>-linux-<x86_64|aarch64>.xz`. Native builds
reuse `$XDG_CACHE_HOME/cosmic-forge/engine-target/<version>` across engine releases;
`COSMIC_FORGE_ENGINE_TARGET` can override it. Neither installer installs system
packages or development tools. The original system compositor remains intact.
Increment `renderer_revision` when changing compositor patches or runtime code;
installer-only fixes keep the existing revision to avoid needless rebuilds.
Renderer revision 2 adds the neutral `glass` pipeline with independent
`forge_blur`, sharp/blurred background, geometry, scale and theme inputs. Aero and
Crystal behavior stays compatible. `glass.frag` is its neutral fallback;
`patches/generic.patch` extends the existing rendering path.
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
CRYSTAL_QA_DIR="$PWD/build/preview" python tests/verify_generic.py
FORGE_SHADERS_DIR=/path/cosmic-forge-shaders \
  CRYSTAL_QA_DIR="$PWD/build/preview" python tests/verify_packs.py
touch build/preview/stop
```

The patches retain the original COSMIC Crystal optics, Aero, native Frosted Glass
integration and upstream notices. GPL-3.0-only; upstream dependencies retain their
own licenses.

The static libdisplay-info dependency retains its MIT notice in `LICENSE.libdisplay-info` and the engine bundle.

Panel preferences use native profiles and optional `top_edge_gradient` and `forge_colors` keys. `forge_colors: Some((light: Some((r,g,b)), dark: None))` selects an RGB color in light mode and follows the COSMIC palette in dark mode, even with Forge closed. Original background preferences and Settings remain intact. Panel revision is independent of the compositor revision.

To update an existing release, dispatch the release workflow with `component=panel` and `publish=true`. It replaces matching assets on the chosen existing tag and preserves other component assets. Forced tag updates run checks without rebuilding; keep that tag on the tested source commit.
