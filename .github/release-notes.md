## COSMIC Forge Engine 0.6.0

New timing and geometry inputs make effects respond to each window's lifetime and the
monitor displaying it. Renderer revision **7**, shader ABI **1**.

### Independent window timing

- Added `window_age`: elapsed seconds since a custom effect first rendered on
  each glass surface. Effects such as accumulating snow can progress separately
  on each window instead of sharing a global timer.
- Moving, resizing, hiding or changing a window's monitor preserves its clock.
  Selecting another custom effect starts a fresh clock. The uniform is zero
  while animation is disabled.

### Monitor-aware shader geometry

- Added `output_size`: the width and height of the monitor currently rendering
  the surface, in logical units, with rotation and fractional scaling applied.
- Added `surface_position`: the surface's top-left position relative to that
  monitor. Together these inputs let perspective effects such as Void look
  straight ahead when a window is centered, without manual display dimensions.
- Geometry updates when windows move between monitors or display settings
  change. When a window spans monitors, each part uses its rendering output's
  dimensions and coordinates.
- Both geometry inputs work with animation disabled. Cached surfaces refresh
  when their geometry changes without keeping static effects continuously
  rendering.

### Compatibility and installation

- Existing shader packs remain compatible with **shader ABI 1**. Packs using
  `window_age` require renderer **6+**; packs using the new geometry inputs
  require renderer **7+**.
- Prebuilt binaries cover pinned COSMIC **1.10.0 and 1.11.0** on **x86_64 and
  ARM64**, with **glibc 2.35+**.
- Install through **Settings → Patches**, then sign out and back in. Restore
  continues to select the original system compositor and preserve preferences.

Timing and monitor geometry are recorded in separate commits. Automated
regressions cover independent clocks, effect changes, multiple outputs,
fractional scaling, rotation and geometry updates with animation disabled.

[Full source changes](https://github.com/motixo/cosmic-forge-engine/compare/engine-v0.5.1...engine-v0.6.0)
