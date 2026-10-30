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

Renderer revision 6 adds `window_age`: seconds since a custom effect first
rendered on each glass surface. Each surface has its own clock, so effects such
as accumulating snow can progress independently on different windows.
Moving, resizing or hiding a window does not restart it; switching to another
custom effect resets it. The uniform is zero when animation is disabled.

Shader ABI 1, animated shaders, scene or wallpaper backgrounds, window focus
and theme inputs remain supported.

GPL-3.0-only. Dependency notices are included in the repository.
