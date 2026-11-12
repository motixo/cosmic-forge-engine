// SPDX-License-Identifier: GPL-3.0-only
use super::{
    blur_effect,
    forge_animation::{self, CLOCK_UNIFORM, Clock},
    forge_effect_map,
    forge_runtime::{
        ANIMATION_FOCUS_KEY, ANIMATION_FPS_KEY, AnimationActivity, Backdrop,
        LEGACY_LOW_ANIMATION_FPS_KEY, RuntimeEffect, resolve_animation_fps,
    },
};
use crate::backend::render::element::AsGlowRenderer;
use smithay::backend::renderer::gles::{GlesRenderer, GlesTexProgram, UniformName, UniformType};
use std::{
    borrow::{Borrow, BorrowMut},
    collections::hash_map::DefaultHasher,
    hash::{Hash, Hasher},
    sync::{Arc, Mutex},
    time::Instant,
};

struct Current {
    revision: u64,
    runtime: Option<Arc<RuntimeEffect>>,
    backdrop: Backdrop,
    clock: Option<Clock>,
    activity: AnimationActivity,
    focus_only: bool,
}

static CURRENT: Mutex<Current> = Mutex::new(Current {
    revision: 0,
    runtime: None,
    backdrop: Backdrop::Scene,
    clock: None,
    activity: AnimationActivity::Static,
    focus_only: false,
});

pub fn configure(config: &cosmic_config::Config) {
    use cosmic_config::ConfigGet;
    forge_animation::set_fps(resolve_animation_fps(
        config.get::<u16>(ANIMATION_FPS_KEY).ok(),
        config.get::<u16>(LEGACY_LOW_ANIMATION_FPS_KEY).ok(),
    ));
    static ANNOUNCE: std::sync::Once = std::sync::Once::new();
    ANNOUNCE.call_once(|| {
        if let Some(directory) = std::env::var_os("XDG_RUNTIME_DIR") {
            let path = std::path::Path::new(&directory).join("cosmic-forge-engine-v1");
            let _ = std::fs::write(path, std::process::id().to_string());
        }
    });
    // Values and shader source arrive in one config key, so render threads see a complete effect.
    let selected = config.get::<String>("glass_effect").unwrap_or_default();
    let runtime = config
        .get::<RuntimeEffect>("forge_runtime")
        .ok()
        .filter(|effect| effect.valid() && effect.id == selected);
    if let Some(effect) = &runtime {
        blur_effect::set_glass_effect(match effect.base.as_str() {
            "glass" => 3,
            "aero" => 2,
            _ => 1,
        });
    }
    let backdrop = runtime.as_ref().map_or_else(
        || config.get::<Backdrop>("forge_backdrop").unwrap_or_default(),
        |effect| effect.backdrop,
    );
    let focus_only = config.get::<bool>(ANIMATION_FOCUS_KEY).unwrap_or_default();
    let mut current = CURRENT.lock().unwrap();
    if current.focus_only != focus_only {
        current.focus_only = focus_only;
        current.revision = current.revision.wrapping_add(1);
    }
    if current.runtime.as_deref() != runtime.as_ref() || current.backdrop != backdrop {
        current.clock = runtime.as_ref().and_then(|effect| {
            let rate = effect.animation_rate()?;
            let now = Instant::now();
            let same_effect = current
                .runtime
                .as_ref()
                .is_some_and(|old| old.id == effect.id);
            let mut clock = current
                .clock
                .filter(|_| same_effect)
                .unwrap_or_else(|| Clock::new(now, rate));
            clock.set_rate(now, rate);
            Some(clock)
        });
        current.activity = runtime
            .as_ref()
            .map_or(AnimationActivity::Static, RuntimeEffect::animation_activity);
        current.revision = current.revision.wrapping_add(1);
        current.runtime = runtime.map(Arc::new);
        current.backdrop = backdrop;
    }
}

pub fn current() -> (
    u64,
    Option<Arc<RuntimeEffect>>,
    Option<Clock>,
    AnimationActivity,
    bool,
) {
    let current = CURRENT.lock().unwrap();
    (
        current.revision,
        current.runtime.clone(),
        current.clock,
        current.activity,
        current.focus_only,
    )
}

pub fn backdrop() -> Backdrop {
    CURRENT.lock().unwrap().backdrop
}

#[derive(Default)]
struct Cache {
    key: Option<u64>,
    program: Option<GlesTexProgram>,
    rain: Option<forge_effect_map::FieldPrograms>,
}

pub fn program<R: AsGlowRenderer>(
    renderer: &mut R,
    effect: &RuntimeEffect,
) -> (
    Option<GlesTexProgram>,
    Option<forge_effect_map::FieldPrograms>,
) {
    let mut hash = DefaultHasher::new();
    effect.source.hash(&mut hash);
    effect.base.hash(&mut hash);
    for parameter in &effect.parameters {
        parameter.name.hash(&mut hash);
    }
    let key = hash.finish();
    let cache = {
        let data = Borrow::<GlesRenderer>::borrow(renderer.glow_renderer())
            .egl_context()
            .user_data();
        data.insert_if_missing_threadsafe(|| Arc::new(Mutex::new(Cache::default())));
        data.get::<Arc<Mutex<Cache>>>().unwrap().clone()
    };
    let mut cache = cache.lock().unwrap();
    if cache.key == Some(key) {
        return (cache.program.clone(), cache.rain.clone());
    }
    let mut uniforms = vec![
        UniformName::new("geo_size", UniformType::_2f),
        UniformName::new("output_size", UniformType::_2f),
        UniformName::new("surface_position", UniformType::_2f),
        UniformName::new("output_scale", UniformType::_1f),
        UniformName::new("corner_radius", UniformType::_4f),
        UniformName::new("input_to_geo", UniformType::Matrix3x3),
        UniformName::new("sharp_tex", UniformType::_1i),
        UniformName::new("blur_amount", UniformType::_1f),
        UniformName::new("theme_tint", UniformType::_3f),
        UniformName::new("theme_accent", UniformType::_3f),
        UniformName::new("uv_per_pixel", UniformType::_2f),
        UniformName::new("refraction_strength", UniformType::_1f),
        UniformName::new("facet_density", UniformType::_1f),
        UniformName::new("crystal_thickness", UniformType::_1f),
        UniformName::new("everyday_mode", UniformType::_1f),
        UniformName::new("aero_color", UniformType::_1f),
        UniformName::new("aero_reflections", UniformType::_1f),
        UniformName::new("time", UniformType::_1f),
        UniformName::new("window_age", UniformType::_1f),
        UniformName::new("delta_time", UniformType::_1f),
        UniformName::new("frame_index", UniformType::_1i),
        UniformName::new("window_position", UniformType::_2f),
        UniformName::new("window_velocity", UniformType::_2f),
        UniformName::new("window_active", UniformType::_1f),
        UniformName::new("is_dark", UniformType::_1f),
    ];
    if effect.animation_rate().is_some() {
        uniforms.push(UniformName::new(CLOCK_UNIFORM, UniformType::_2f));
    }
    uniforms.extend(
        effect
            .parameters
            .iter()
            .map(|p| UniformName::new(p.name.clone(), UniformType::_1f)),
    );
    let source = if forge_effect_map::Kind::from_source(&effect.source).is_some() {
        uniforms.push(UniformName::new("forge_effect_map_tex", UniformType::_1i));
        uniforms.push(UniformName::new("forge_effect_map_shade", UniformType::_1i));
        uniforms.push(UniformName::new(
            "forge_effect_map_enabled",
            UniformType::_1f,
        ));
        std::borrow::Cow::Owned(effect.source.replacen(
            "//_DEFINES_",
            "//_DEFINES_\n#define FORGE_EFFECT_MAP_SAMPLE",
            1,
        ))
    } else {
        std::borrow::Cow::Borrowed(effect.source.as_str())
    };
    let renderer = BorrowMut::<GlesRenderer>::borrow_mut(renderer.glow_renderer_mut());
    let result = renderer.compile_custom_texture_shader(source.as_ref(), &uniforms);
    cache.rain = if result.is_ok()
        && effect
            .source
            .starts_with("// forge-effect-map: rain-runoff-v1\n// forge-rain-field: v1\n")
    {
        match forge_effect_map::FieldPrograms::new(renderer, source.as_ref(), &uniforms) {
            Ok(programs) => Some(programs),
            Err(error) => {
                tracing::info!(?error, "Full-resolution Rain programs unavailable");
                None
            }
        }
    } else {
        None
    };
    cache.program = match result {
        Ok(program) => Some(program),
        Err(error) => {
            tracing::warn!(
                effect = effect.id,
                ?error,
                "Forge shader rejected; using built-in glass"
            );
            None
        }
    };
    cache.key = Some(key);
    (cache.program.clone(), cache.rain.clone())
}
