## Changes since engine-v0.4.0

This release introduces renderer revision **4**, while retaining shader ABI **1**.

### Added

- Opt-in animation on all custom shader bases (`glass`, `aero` and `crystal`):
  `shader.animated` enables `time`, `delta_time` and `frame_index`. Static packs
  keep their existing behavior. Visible animated surfaces request frames;
  hidden, minimized and fully covered surfaces stop driving animation redraws.
- Window motion inputs: `window_position` and `window_velocity` let shaders react
  to dragging without another compositor patch.
- Background source selection through `shader.backdrop`: `scene` remains the
  default; `wallpaper` samples only COSMIC's wallpaper layer, excluding windows,
  panels and other desktop content. Both `tex` and `sharp_tex` use the selected
  source.
- Wallpaper image and blur caches per output and graphics context. Sampling
  follows window position, wallpaper layout, output scale and rotation. Moving
  windows does not rebuild an unchanged wallpaper image or blur.
- Engine-provided `window_active` and `is_dark` uniforms. Dependent surfaces
  inherit their owner's activation; ownerless surfaces default to active.
  Theme state comes from the system theme mode. Focus and theme changes redraw
  static shaders without recompiling them.

### Fixed

- `sharp_tex` retains the original background at maximum blur on every custom
  base, including Aero and Crystal.
- Wallpaper effects render without a framebuffer capture cache and preserve
  foreground pixels and clipping. A missing wallpaper layer produces a black
  backdrop instead of leaking desktop content into wallpaper-only mode.

### Compatibility and installation

- Use **COSMIC Forge 0.11.1** for per-effect background selection and installed
  component information. Existing static scene packs remain compatible.
- Prebuilt compositors cover the pinned **COSMIC 1.10.0 and 1.11.0** releases on
  **x86_64 and ARM64**, with **glibc 2.35+**, for Arch Linux, Fedora, Ubuntu and
  Pop!_OS installations with a matching COSMIC version.
- Install through **Forge → Settings → Patches**, or run `python3 install.py`
  from the engine repository. `python3 install.py --build` builds from pinned
  source. Sign out and back in after installing or switching compositors.
- Matching binaries are reused. Restore selects the original system compositor
  and preserves effect preferences and recovery data.
- These are renderer capabilities; shader code supplies the animation and
  visual design. External image textures, extra render passes and previous-frame
  feedback are not introduced by this release.

### Validation

Local checks passed: 34 Python tests; native animation and GPU cache tests;
45 isolated background/focus/theme render checks; and 61 generic, Aero and
animation regression checks. Output scale and rotation were exercised in a
nested compositor; separate virtual outputs were exercised in the GPU cache
test. Physical multi-monitor hardware was not tested.

[Full source changes](https://github.com/motixo/cosmic-forge-engine/compare/engine-v0.4.0...engine-v0.5.0)
