// SPDX-License-Identifier: GPL-3.0-only
// Shared wire format: copied into the compositor by engine/build.py.
use serde::{Deserialize, Serialize};

pub const ABI: u32 = 1;
pub const MAX_SHADER: usize = 64 * 1024;
pub const ANIMATION_FPS: [u16; 6] = [15, 20, 30, 60, 120, 0];
pub const ANIMATION_FPS_KEY: &str = "forge_animation_fps";
pub const ANIMATION_FOCUS_KEY: &str = "forge_animation_focus_only";
pub const LEGACY_LOW_ANIMATION_FPS_KEY: &str = "forge_animation_fps_low";

pub fn reserved_control_key(key: &str) -> bool {
    matches!(
        key,
        "forge_runtime"
            | "forge_backdrop"
            | ANIMATION_FPS_KEY
            | ANIMATION_FOCUS_KEY
            | LEGACY_LOW_ANIMATION_FPS_KEY
    )
}

pub fn resolve_animation_fps(fps: Option<u16>, old_low_fps: Option<u16>) -> u16 {
    match (fps, old_low_fps) {
        (Some(30), Some(low @ (15 | 20))) => low,
        (Some(fps), _) if ANIMATION_FPS.contains(&fps) => fps,
        _ => 0,
    }
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Backdrop {
    #[default]
    Scene,
    Wallpaper,
}

impl Backdrop {
    pub fn is_scene(&self) -> bool {
        *self == Self::Scene
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Parameter {
    pub key: String,
    pub name: String,
    pub value: f32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeEffect {
    pub abi: u32,
    pub id: String,
    pub base: String,
    pub source: String,
    #[serde(default, skip_serializing_if = "is_false")]
    pub animated: bool,
    #[serde(default, skip_serializing_if = "Backdrop::is_scene")]
    pub backdrop: Backdrop,
    pub parameters: Vec<Parameter>,
}

pub fn is_false(value: &bool) -> bool {
    !*value
}

pub fn identifier(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum AnimationActivity {
    Static,
    Continuous,
    Until(f32),
}

impl AnimationActivity {
    pub fn needs_frame(self, age: f32) -> bool {
        match self {
            Self::Static => false,
            Self::Continuous => true,
            Self::Until(seconds) => age < seconds,
        }
    }
}

impl RuntimeEffect {
    pub fn animation_rate(&self) -> Option<f64> {
        self.parameters
            .iter()
            .find(|p| p.name == "forge_animation_speed_percent")
            .map(|p| (f64::from(p.value) / 100.0).clamp(0.25, 3.0))
    }

    pub fn animation_activity(&self) -> AnimationActivity {
        use AnimationActivity::{Continuous, Static, Until};
        if !self.animated {
            return Static;
        }
        // A GLSL comment lets older Forge versions load the same shader.
        let Some(rule) = self
            .source
            .lines()
            .find_map(|line| line.trim_start().strip_prefix("// forge-animation-idle:"))
        else {
            return Continuous;
        };
        let value = |name: &str| {
            self.parameters
                .iter()
                .find(|p| p.name == name)
                .map(|p| p.value)
        };
        let words: Vec<_> = rule.split_whitespace().take(5).collect();
        match words.as_slice() {
            ["zero", uniform] if value(uniform).is_some_and(|v| v <= 0.0) => Static,
            ["accumulation", enabled, duration, density] => {
                match (value(enabled), value(duration), value(density)) {
                    (Some(enabled), Some(seconds), Some(_)) if enabled >= 0.5 => {
                        Until(seconds.max(1.0))
                    }
                    (Some(_), Some(_), Some(density)) if density <= 0.0 => Static,
                    _ => Continuous,
                }
            }
            _ => Continuous,
        }
    }

    pub fn glass_blur(&self) -> u8 {
        self.parameters
            .iter()
            .find(|p| p.name == "forge_blur")
            .map_or(45, |p| p.value.clamp(0.0, 100.0) as u8)
    }

    pub fn valid(&self) -> bool {
        self.abi == ABI
            && !self.id.is_empty()
            && self.id.len() <= 64
            && self
                .id
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
            && matches!(self.base.as_str(), "crystal" | "aero" | "glass")
            && !self.source.trim().is_empty()
            && self.source.len() <= MAX_SHADER
            && !self.source.contains('\0')
            && self.parameters.len() <= 32
            && self.parameters.iter().enumerate().all(|(i, p)| {
                identifier(&p.key)
                    && !reserved_control_key(&p.key)
                    && identifier(&p.name)
                    && p.name.starts_with("forge_")
                    && p.value.is_finite()
                    && p.value.abs() <= 10000.0
                    && self.parameters[..i]
                        .iter()
                        .all(|other| other.name != p.name)
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_explicit_animation_idle_rules_can_stop_an_animated_shader() {
        let effect = |source: &str, values: &[(&str, f32)]| RuntimeEffect {
            abi: ABI,
            id: "snow".into(),
            base: "glass".into(),
            source: source.into(),
            animated: true,
            backdrop: Backdrop::Scene,
            parameters: values
                .iter()
                .map(|&(name, value)| Parameter {
                    key: name.into(),
                    name: name.into(),
                    value,
                })
                .collect(),
        };
        let zero = "// forge-animation-idle: zero forge_strength";
        assert!(
            !effect(zero, &[("forge_strength", 0.0)])
                .animation_activity()
                .needs_frame(0.0)
        );
        assert!(
            effect(zero, &[("forge_strength", 1.0)])
                .animation_activity()
                .needs_frame(0.0)
        );
        assert!(effect(zero, &[]).animation_activity().needs_frame(0.0));
        assert!(
            effect("", &[("forge_animation_intensity_percent", 0.0)])
                .animation_activity()
                .needs_frame(0.0)
        );
        let rule = "// forge-animation-idle: accumulation forge_snow_accumulation forge_snow_fill_time forge_snow_density";
        let snow = |source, accumulation, density| {
            effect(
                source,
                &[
                    ("forge_snow_accumulation", accumulation),
                    ("forge_snow_density", density),
                    ("forge_snow_fill_time", 10.0),
                ],
            )
            .animation_activity()
        };
        assert!(snow(rule, 1.0, 75.0).needs_frame(9.0));
        assert!(snow(rule, 1.0, 0.0).needs_frame(9.0));
        assert!(!snow(rule, 1.0, 75.0).needs_frame(10.0));
        assert!(snow(rule, 0.0, 75.0).needs_frame(20.0));
        assert!(!snow(rule, 0.0, 0.0).needs_frame(0.0));
        assert!(snow("", 1.0, 75.0).needs_frame(20.0));
    }
}
