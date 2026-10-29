# COSMIC Forge Engine

Custom shader support for [COSMIC Forge](https://github.com/motixo/cosmic-forge).
Includes the compositor patches, shader runtime and installer.
[Shader packs](https://github.com/motixo/cosmic-forge-shaders) are maintained separately.

Supports pinned COSMIC 1.10/1.11 releases on Arch Linux, Fedora, Ubuntu and Pop!_OS.
Prebuilt binaries are available for x86_64 and ARM64 with glibc 2.35+.

## Install

Open **Settings → Patches** in Forge, or run from this repository:

```sh
python3 install.py          # Install the matching binary
python3 install.py --build  # Build from pinned source instead
python3 recovery.py         # Restore the original system compositor
```

Matching binaries are reused. Restore preserves settings, shaders and recovery
files. Sign out and back in after switching compositors.

## Development

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 build.py --cosmic-version 1.10.0
```

Requires Python 3.11+, Rust, Git and COSMIC compositor development libraries.
Upstream commits and the shader ABI are pinned in `release.json`.

Renderer revision 5 supports animated shaders, scene or wallpaper backgrounds,
and window focus and theme inputs, including wallpaper rendering on built-in effects.

GPL-3.0-only. Dependency notices are included in the repository.
