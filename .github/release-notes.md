## Changes since engine-v0.5.0

Renderer revision **5**, shader ABI **1**.

### Fixed

- Fixed Wallpaper only rendering on built-in Frosted Glass and Aero. The engine
  sent `uv_per_pixel` to shaders that did not register it, causing repeated
  rendering errors and a potentially unusable desktop session.
- Send wallpaper pixel-step uniforms only to programs that support them.
  Custom shaders and invalid-shader fallbacks retain the appropriate mapping.
- Extended GPU regressions to exercise Frosted, Aero and Crystal built-ins,
  all three custom bases, and invalid custom-shader fallbacks. The GPU test now
  runs on every release build instead of being skipped in CI.

### Installation

- Use **Forge 0.11.2**. Its background picker is hidden until a compatible
  renderer is installed, and wallpaper packs require renderer revision 5.
- This release replaces engine-v0.5.0, which was withdrawn from Latest after
  the Frosted/Aero issue was confirmed.
- Install through **Settings → Patches**, then sign out and back in. Restore
  continues to select the original system compositor and preserve preferences.
- Prebuilt binaries cover pinned COSMIC **1.10.0 and 1.11.0**, **x86_64 and ARM64**,
  and **glibc 2.35+**. Animation, wallpaper caching and window-state inputs remain
  available with shader ABI 1.

[Full source changes](https://github.com/motixo/cosmic-forge-engine/compare/engine-v0.5.0...engine-v0.5.1)
