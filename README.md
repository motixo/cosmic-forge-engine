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

Renderer revision 7 retains shader ABI 1 and adds the following shader inputs:

| Uniform | Available since | Meaning |
| --- | --- | --- |
| `window_age` | Renderer 6 | Seconds since this custom effect first rendered on this glass surface. |
| `output_size` | Renderer 7 | Current rendering monitor width and height in logical units. |
| `surface_position` | Renderer 7 | Surface top-left relative to that monitor, in logical units. |

Each surface has its own `window_age` clock. Moving, resizing or hiding a window
does not restart it; switching to another custom effect resets it. The uniform
is zero when animation is disabled. This supports effects such as snow that
accumulates independently on each window.

`output_size` accounts for monitor rotation and fractional scaling. Geometry
inputs remain available with animation disabled and update as windows move or
outputs change, without requesting continuous redraws. A window spanning
monitors receives the geometry of the output currently rendering each part.
For a centered perspective, use
`surface_position + geo_size * 0.5 - output_size * 0.5`.

Existing animated shaders, scene or wallpaper backgrounds, window focus and
theme inputs remain supported.

GPL-3.0-only. Dependency notices are included in the repository.
