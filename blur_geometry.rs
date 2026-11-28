// SPDX-License-Identifier: GPL-3.0-only
use smithay::utils::{Buffer, Physical, Point, Rectangle, Size};

pub(super) fn prefer_partial_capture(rect: Rectangle<i32, Physical>, dst: Rectangle<i32, Physical>) -> bool {
    i64::from(rect.size.w) * i64::from(rect.size.h) * 2 < i64::from(dst.size.w) * i64::from(dst.size.h)
}

// Propagate the visible bounds backwards through the unchanged sampling grids.
pub(super) fn blur_footprint(
    output: Rectangle<i32, Buffer>,
    full: Size<i32, Buffer>,
    offset: f64,
    passes: usize,
) -> Vec<Rectangle<i32, Buffer>> {
    let mut footprint = vec![output; 2 * passes + 1];
    for step in (0..2 * passes).rev() {
        let source_level = step.min(2 * passes - step);
        let target_level = (step + 1).min(2 * passes - step - 1);
        let source = full.downscale(1 << source_level);
        let target = full.downscale(1 << target_level);
        let needed = footprint[step + 1];
        let bound = |lo: i32, hi: i32, full: i32, source: i32, target: i32| {
            let ratio = f64::from(source) / f64::from(target);
            let tap = if source_level > target_level { 1.0 } else { 0.5 };
            // Two extra texels cover bilinear support and coordinate rounding.
            let radius = tap * offset * f64::from(full) / f64::from(source << source_level) + 2.0;
            let lo = ((f64::from(lo) * ratio - radius).floor() as i32).clamp(0, source);
            let hi = ((f64::from(hi) * ratio + radius).ceil() as i32).clamp(lo, source);
            (lo, hi)
        };
        let (x0, x1) = bound(needed.loc.x, needed.loc.x + needed.size.w, full.w, source.w, target.w);
        let (y0, y1) = bound(needed.loc.y, needed.loc.y + needed.size.h, full.h, source.h, target.h);
        footprint[step] = Rectangle::new(Point::new(x0, y0), Size::new(x1 - x0, y1 - y0));
    }
    footprint
}

pub(super) fn pass_damage(
    damage: Rectangle<i32, Buffer>,
    full: Size<i32, Buffer>,
    source_level: usize,
    target_level: usize,
    offset: f64,
) -> Rectangle<i32, Buffer> {
    let factor = 1 << source_level;
    let src = full.downscale(factor);
    let dst = full.downscale(1 << target_level);
    let bounds = |start: i32, end: i32, source: i32, target: i32, original: i32| {
        // Include the widest Kawase tap plus the footprint of bilinear filtering.
        let radius = offset * f64::from(original) / (f64::from(source) * f64::from(factor)) + 2.0;
        let scale = f64::from(target) / f64::from(source);
        let lo = (((f64::from(start) - radius) * scale).floor() as i32).clamp(0, target);
        let hi = (((f64::from(end) + radius) * scale).ceil() as i32).clamp(lo, target);
        (lo, hi)
    };
    let (x1, x2) = bounds(damage.loc.x, damage.loc.x + damage.size.w, src.w, dst.w, full.w);
    let (y1, y2) = bounds(damage.loc.y, damage.loc.y + damage.size.h, src.h, dst.h, full.h);
    Rectangle::new(Point::from((x1, y1)), Size::from((x2 - x1, y2 - y1)))
}

pub(super) fn blur_half_pixel(full: Size<i32, Buffer>, storage: Size<i32, Buffer>, content: Size<i32, Buffer>) -> [f32; 2] {
    [
        0.5 / content.w as f32 * (full.w as f32 / storage.w as f32),
        0.5 / content.h as f32 * (full.h as f32 / storage.h as f32),
    ]
}
