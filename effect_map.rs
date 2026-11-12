// SPDX-License-Identifier: GPL-3.0-only
use super::{forge_animation, forge_runtime::RuntimeEffect};
use smithay::{
    backend::renderer::{
        Bind, Frame, FrameContext, Renderer, Texture,
        element::Id,
        gles::{
            GlesError, GlesFrame, GlesPixelProgram, GlesRenderer, GlesTexProgram, GlesTexture,
            Uniform, UniformName, UniformType, ffi,
        },
    },
    utils::{Buffer, Physical, Rectangle, Size, Transform},
};
use std::{
    cell::{Cell, RefCell},
    ffi::CStr,
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug)]
pub enum Kind {
    Rain(u32),
    RainRunoff,
    Ember,
}

impl Kind {
    pub fn from_source(source: &str) -> Option<Self> {
        let source = source
            .strip_prefix("// forge-backdrop: blurred-only\n")
            .unwrap_or(source);
        if source.starts_with("// forge-effect-map: rain-runoff-v1\n") {
            Some(Self::RainRunoff)
        } else if source.starts_with("// forge-effect-map: rain-v1\n") {
            Some(Self::Rain(75))
        } else if source.starts_with("// forge-effect-map: rain-v1-60\n") {
            Some(Self::Rain(60))
        } else if source.starts_with("// forge-effect-map: rain-v1-45\n") {
            Some(Self::Rain(45))
        } else if source.starts_with("// forge-effect-map: ember-v1\n") {
            Some(Self::Ember)
        } else {
            None
        }
    }

    pub fn size(self, physical: Size<f64, Physical>, logical_width: f64) -> Size<i32, Buffer> {
        let (numerator, denominator) = match self {
            Self::Rain(percent) => (i64::from(percent), 100),
            Self::Ember => (1, 4),
            Self::RainRunoff => {
                // Four six-row slots, indexed by the smallest possible lane width.
                let columns = (logical_width as f32 / 18.0).floor() as i32;
                return (columns.saturating_add(1), 24).into();
            }
        };
        let dimension =
            |pixels: f64| ((pixels as i64 * numerator + denominator - 1) / denominator) as i32;
        (dimension(physical.w), dimension(physical.h)).into()
    }

    fn uniforms(self) -> &'static [(&'static str, UniformType)] {
        match self {
            Self::Rain(_) | Self::RainRunoff => &RAIN_UNIFORMS,
            Self::Ember => &EMBER_UNIFORMS,
        }
    }

    fn amount(self) -> &'static str {
        match self {
            Self::Rain(_) | Self::RainRunoff => "forge_animation_intensity_percent",
            Self::Ember => "forge_ember_strength",
        }
    }
}

const RAIN_UNIFORMS: [(&str, UniformType); 7] = [
    ("geo_size", UniformType::_2f),
    ("output_scale", UniformType::_1f),
    ("time", UniformType::_1f),
    ("forge_animation_clock", UniformType::_2f),
    ("forge_animation_intensity_percent", UniformType::_1f),
    ("forge_animation_speed_percent", UniformType::_1f),
    ("forge_rain_size", UniformType::_1f),
];

const EMBER_UNIFORMS: [(&str, UniformType); 6] = [
    ("geo_size", UniformType::_2f),
    ("time", UniformType::_1f),
    ("theme_accent", UniformType::_3f),
    ("forge_ember_strength", UniformType::_1f),
    ("forge_ember_scale", UniformType::_1f),
    ("forge_ember_color", UniformType::_1f),
];

#[derive(Clone, PartialEq, Eq)]
pub struct Key {
    pub context: usize,
    pub revision: u64,
    pub size: Size<i32, Buffer>,
    pub logical_size: [u32; 2],
    pub scale: u32,
    pub accent: [u32; 3],
    pub surface: Option<Id>,
}

struct Map {
    key: Key,
    program: GlesPixelProgram,
    texture: GlesTexture,
    shade: Option<GlesTexture>,
    kind: Kind,
    sample: Option<Instant>,
    last_used: Instant,
}

#[derive(Default)]
struct Cache {
    maps: Vec<Map>,
    failed: Option<(usize, u64)>,
}

thread_local! {
    // These textures are only sampled in the exact EGL context that renders them.
    static CACHE: RefCell<Cache> = RefCell::new(Cache::default());
}

pub fn expire(now: Instant) {
    FIELD.with_borrow_mut(|field| {
        if field
            .as_ref()
            .is_some_and(|f| now.saturating_duration_since(f.last_used) >= Duration::from_secs(1))
        {
            field.take();
        }
    });
    CACHE.with_borrow_mut(|cache| {
        cache
            .maps
            .retain(|map| now.saturating_duration_since(map.last_used) < Duration::from_secs(1));
    });
}

pub fn get(
    frame: &mut GlesFrame<'_, '_>,
    key: &Key,
    effect: &RuntimeEffect,
    kind: Kind,
    uniforms: &[Uniform<'_>],
    sample: Instant,
) -> Option<(GlesTexture, Option<GlesTexture>)> {
    if !effect
        .parameters
        .iter()
        .any(|parameter| parameter.name == kind.amount() && parameter.value > 0.0)
    {
        return None;
    }
    let now = Instant::now();
    CACHE.with_borrow_mut(|cache| {
        let revision = (key.context, key.revision);
        if cache.failed == Some(revision) {
            return None;
        }
        cache
            .maps
            .retain(|map| (map.key.context, map.key.revision) == revision);
        let index = if let Some(index) = cache.maps.iter().position(|map| &map.key == key) {
            index
        } else {
            let pixels = |size: Size<i32, Buffer>| i64::from(size.w) * i64::from(size.h);
            // Bound memory without reallocating when extra windows share the output.
            if cache.maps.len() >= 4
                || pixels(key.size)
                    + cache
                        .maps
                        .iter()
                        .map(|map| pixels(map.key.size))
                        .sum::<i64>()
                    > 5 * 1024 * 1024
            {
                return None;
            }
            match Map::new(frame, key, effect, kind, now) {
                Ok(map) => {
                    cache.maps.push(map);
                    cache.maps.len() - 1
                }
                Err(error) => {
                    cache.failed = Some(revision);
                    tracing::info!(
                        ?error,
                        ?kind,
                        "Forge effect map unavailable; using direct shader"
                    );
                    return None;
                }
            }
        };
        let map = &mut cache.maps[index];
        if map.sample.is_none() || (effect.animated && map.sample != Some(sample)) {
            if let Err(error) = map.render(frame, uniforms, sample) {
                cache.maps.remove(index);
                cache.failed = Some(revision);
                tracing::info!(
                    ?error,
                    ?kind,
                    "Forge effect map failed; using direct shader"
                );
                return None;
            }
        }
        map.last_used = now;
        Some((map.texture.clone(), map.shade.clone()))
    })
}

impl Map {
    fn new(
        frame: &mut GlesFrame<'_, '_>,
        key: &Key,
        effect: &RuntimeEffect,
        kind: Kind,
        now: Instant,
    ) -> Result<Self, GlesError> {
        let mut guard = frame.renderer();
        let renderer = guard.as_mut();
        let extension = match kind {
            Kind::Ember => "",
            Kind::Rain(_) | Kind::RainRunoff => renderer
                .with_context(|gl| unsafe {
                    let list = gl.GetString(ffi::EXTENSIONS);
                    if list.is_null() {
                        return None;
                    }
                    let list = CStr::from_ptr(list.cast()).to_bytes();
                    let has =
                        |extension: &[u8]| list.split(|b| *b == b' ').any(|name| name == extension);
                    if !has(b"GL_EXT_color_buffer_float") {
                        None
                    } else if matches!(kind, Kind::RainRunoff) {
                        Some("")
                    } else if has(b"GL_EXT_draw_buffers") {
                        Some("#extension GL_EXT_draw_buffers : require\n")
                    } else if has(b"GL_NV_draw_buffers") {
                        Some("#extension GL_NV_draw_buffers : require\n")
                    } else {
                        None
                    }
                })?
                .ok_or(GlesError::UnknownPixelFormat)?,
        };
        let header = format!("{extension}#define FORGE_EFFECT_MAP_PASS");
        // Forge can append a final-composition wrapper for glass grain.
        let source = effect
            .source
            .split_once("\n// forge-effect-map-end\n")
            .map_or(effect.source.as_str(), |(source, _)| source)
            .replacen("#version 100", &header, 1);
        let names: Vec<_> = kind
            .uniforms()
            .iter()
            .map(|&(name, uniform_type)| UniformName::new(name, uniform_type))
            .collect();
        let program = renderer.compile_custom_pixel_shader(source, &names)?;
        let (texture, shade) = match kind {
            Kind::Rain(_) => (
                field_texture(renderer, key.size, ffi::RGBA16F, ffi::RGBA, ffi::HALF_FLOAT)?,
                Some(field_texture(
                    renderer,
                    key.size,
                    ffi::R16F,
                    ffi::RED,
                    ffi::HALF_FLOAT,
                )?),
            ),
            Kind::RainRunoff => (
                field_texture(renderer, key.size, ffi::RGBA32F, ffi::RGBA, ffi::FLOAT)?,
                None,
            ),
            Kind::Ember => (
                field_texture(
                    renderer,
                    key.size,
                    ffi::RGBA8,
                    ffi::RGBA,
                    ffi::UNSIGNED_BYTE,
                )?,
                None,
            ),
        };
        Ok(Self {
            key: key.clone(),
            program,
            texture,
            shade,
            kind,
            sample: None,
            last_used: now,
        })
    }

    fn render(
        &mut self,
        frame: &mut GlesFrame<'_, '_>,
        uniforms: &[Uniform<'_>],
        sample: Instant,
    ) -> Result<(), GlesError> {
        let mut guard = frame.renderer();
        let renderer = guard.as_mut();
        let size = self.key.size;
        let area = Rectangle::from_size(size.to_logical(1, Transform::Normal).to_physical(1));
        let mut target = renderer.bind(&mut self.texture)?;
        let mut pass = renderer.render(&mut target, area.size, Transform::Normal)?;
        let ready = pass.with_context(|gl| unsafe {
            gl.Disable(ffi::BLEND);
            let Some(shade) = self.shade.as_ref() else {
                return gl.CheckFramebufferStatus(ffi::FRAMEBUFFER) == ffi::FRAMEBUFFER_COMPLETE;
            };
            gl.FramebufferTexture2D(
                ffi::FRAMEBUFFER,
                ffi::COLOR_ATTACHMENT1,
                ffi::TEXTURE_2D,
                shade.tex_id(),
                0,
            );
            gl.DrawBuffers(2, [ffi::COLOR_ATTACHMENT0, ffi::COLOR_ATTACHMENT1].as_ptr());
            gl.CheckFramebufferStatus(ffi::FRAMEBUFFER) == ffi::FRAMEBUFFER_COMPLETE
        })?;
        let result = if ready {
            let uniforms: Vec<_> = uniforms
                .iter()
                .filter(|uniform| {
                    self.kind
                        .uniforms()
                        .iter()
                        .any(|(name, _)| *name == uniform.name)
                })
                .cloned()
                .collect();
            pass.render_pixel_shader_to(
                &self.program,
                Rectangle::from_size(size.to_f64()),
                area,
                size,
                None,
                1.0,
                &uniforms,
            )
        } else {
            Err(GlesError::UnknownPixelFormat)
        };
        if self.shade.is_some() {
            pass.with_context(|gl| unsafe {
                gl.DrawBuffers(1, [ffi::COLOR_ATTACHMENT0].as_ptr());
                gl.FramebufferTexture2D(
                    ffi::FRAMEBUFFER,
                    ffi::COLOR_ATTACHMENT1,
                    ffi::TEXTURE_2D,
                    0,
                    0,
                );
            })?;
        }
        result?;
        let _sync = pass.finish()?;
        if self.sample.is_none() {
            tracing::info!(
                kind = ?self.kind,
                width = size.w,
                height = size.h,
                "Rendering Forge effect map"
            );
        }
        self.sample = Some(sample);
        Ok(())
    }
}

fn field_texture(
    renderer: &mut GlesRenderer,
    size: Size<i32, Buffer>,
    internal: u32,
    format: u32,
    data_type: u32,
) -> Result<GlesTexture, GlesError> {
    let id = renderer.with_context(|gl| unsafe {
        let (mut id, mut previous) = (0, 0);
        gl.GetIntegerv(ffi::TEXTURE_BINDING_2D, &mut previous);
        gl.GenTextures(1, &mut id);
        gl.BindTexture(ffi::TEXTURE_2D, id);
        gl.TexImage2D(
            ffi::TEXTURE_2D,
            0,
            internal as i32,
            size.w,
            size.h,
            0,
            format,
            data_type,
            std::ptr::null(),
        );
        let filter = if matches!(internal, ffi::RGBA32F | ffi::R32F) {
            ffi::NEAREST
        } else {
            ffi::LINEAR
        };
        for parameter in [ffi::TEXTURE_MIN_FILTER, ffi::TEXTURE_MAG_FILTER] {
            gl.TexParameteri(ffi::TEXTURE_2D, parameter, filter as i32);
        }
        for parameter in [ffi::TEXTURE_WRAP_S, ffi::TEXTURE_WRAP_T] {
            gl.TexParameteri(ffi::TEXTURE_2D, parameter, ffi::CLAMP_TO_EDGE as i32);
        }
        gl.BindTexture(ffi::TEXTURE_2D, previous as u32);
        id
    })?;
    Ok(unsafe { GlesTexture::from_raw(renderer, Some(internal), false, id, size) })
}

#[derive(Clone)]
pub struct FieldPrograms {
    generate: GlesTexProgram,
    pub sample: GlesTexProgram,
}

impl FieldPrograms {
    pub fn new(
        renderer: &mut GlesRenderer,
        source: &str,
        uniforms: &[UniformName<'_>],
    ) -> Result<Self, GlesError> {
        let extension = renderer
            .with_context(|gl| unsafe {
                let list = gl.GetString(ffi::EXTENSIONS);
                if list.is_null() {
                    return None;
                }
                let list = CStr::from_ptr(list.cast()).to_bytes();
                let has =
                    |extension: &[u8]| list.split(|b| *b == b' ').any(|name| name == extension);
                if !has(b"GL_EXT_color_buffer_float") {
                    None
                } else if has(b"GL_EXT_draw_buffers") {
                    Some("GL_EXT_draw_buffers")
                } else if has(b"GL_NV_draw_buffers") {
                    Some("GL_NV_draw_buffers")
                } else {
                    None
                }
            })?
            .ok_or(GlesError::UnknownPixelFormat)?;
        let raw = source
            .split_once("\n// forge-effect-map-end\n")
            .map_or(source, |(raw, _)| raw);
        let generate = raw
            .replacen("#define main forge_glass_original_main\n", "", 1)
            .replacen(
                "//_DEFINES_",
                &format!(
                    "//_DEFINES_\n#extension {extension} : require\n#define FORGE_RAIN_FIELD_PASS"
                ),
                1,
            );
        let sample = source.replacen(
            "//_DEFINES_",
            "//_DEFINES_\n#define FORGE_RAIN_FIELD_SAMPLE",
            1,
        );
        let mut sample_uniforms = uniforms.to_vec();
        sample_uniforms.push(UniformName::new("forge_rain_field_size", UniformType::_2f));
        Ok(Self {
            generate: renderer.compile_custom_texture_shader(&generate, uniforms)?,
            sample: renderer.compile_custom_texture_shader(&sample, &sample_uniforms)?,
        })
    }
}

#[derive(Default)]
pub struct BackdropMotion {
    last: Option<Instant>,
    repeated: Option<Instant>,
    pending: bool,
}

impl BackdropMotion {
    pub fn capture(&mut self, changed: bool) {
        if !changed {
            *self = Self::default();
            return;
        }
        let sample = forge_animation::sample_time();
        let previous = self.last.replace(sample);
        let same_tick = previous == Some(sample);
        self.pending = same_tick
            || self.repeated.is_some_and(|repeated| {
                previous == Some(repeated)
                    && sample.saturating_duration_since(repeated) < Duration::from_millis(60)
            });
        if same_tick {
            static REPEATED: std::sync::Once = std::sync::Once::new();
            REPEATED.call_once(|| {
                tracing::info!("Observed multiple Rain backdrop updates in one tick")
            });
            self.repeated = Some(sample);
        }
    }

    pub fn take(&mut self) -> bool {
        std::mem::take(&mut self.pending) && self.last == Some(forge_animation::sample_time())
    }
}

#[derive(PartialEq)]
struct FieldKey {
    effect: Key,
    output: Size<i32, Physical>,
    transform: Transform,
    source_size: Size<i32, Buffer>,
    src: Rectangle<f64, Buffer>,
    dst: Rectangle<i32, Physical>,
}

struct Field {
    key: FieldKey,
    geometry: GlesTexture,
    shade: GlesTexture,
    sample: Option<Instant>,
    last_used: Instant,
}

thread_local! {
    // The field is sampled in framebuffer coordinates and stays on the rendering context.
    static FIELD: RefCell<Option<Field>> = const { RefCell::new(None) };
    static FAILED_FIELD: Cell<Option<(usize, u64)>> = const { Cell::new(None) };
}

pub fn field_is_current(sample: Instant) -> bool {
    FIELD.with_borrow(|field| field.as_ref().is_some_and(|f| f.sample == Some(sample)))
}

#[allow(clippy::too_many_arguments)]
pub fn rain_field(
    frame: &mut GlesFrame<'_, '_>,
    key: &Key,
    programs: &FieldPrograms,
    source: &GlesTexture,
    src: Rectangle<f64, Buffer>,
    dst: Rectangle<i32, Physical>,
    atlas: &GlesTexture,
    uniforms: &[Uniform<'_>],
    can_generate: bool,
    sample: Instant,
) -> Option<(GlesTexture, GlesTexture)> {
    let revision = (key.context, key.revision);
    if FAILED_FIELD.get() == Some(revision) {
        return None;
    }
    let transform = frame.transformation();
    let output = transform.invert().transform_size(frame.output_size());
    if i64::from(output.w) * i64::from(output.h) > 4 * 1024 * 1024 {
        return None;
    }
    let key = FieldKey {
        effect: key.clone(),
        output,
        transform,
        source_size: source.size(),
        src,
        dst,
    };
    let now = Instant::now();
    FIELD.with_borrow_mut(|field| {
        if !can_generate {
            return field
                .as_ref()
                .filter(|f| f.key == key && f.sample == Some(sample))
                .map(|f| (f.geometry.clone(), f.shade.clone()));
        }
        if field.as_ref().is_some_and(|f| f.key != key) {
            // Do not alternate a large allocation between competing windows.
            if field.as_ref().unwrap().key.effect.context == key.effect.context
                && now.saturating_duration_since(field.as_ref().unwrap().last_used)
                    < Duration::from_millis(100)
            {
                return None;
            }
            field.take();
        }
        let result = (|| -> Result<(GlesTexture, GlesTexture), GlesError> {
            if field.is_none() {
                let size = (output.w, output.h).into();
                let mut guard = frame.renderer();
                let renderer = guard.as_mut();
                let geometry = field_texture(renderer, size, ffi::RGBA32F, ffi::RGBA, ffi::FLOAT)?;
                let shade = field_texture(renderer, size, ffi::R32F, ffi::RED, ffi::FLOAT)?;
                tracing::info!(?output, "Allocated full-resolution Rain field");
                *field = Some(Field {
                    key,
                    geometry,
                    shade,
                    sample: None,
                    last_used: now,
                });
            }
            let field = field.as_mut().unwrap();
            if field.sample != Some(sample) {
                field.render(frame, programs, source, src, dst, atlas, uniforms)?;
                field.sample = Some(sample);
            }
            field.last_used = now;
            Ok((field.geometry.clone(), field.shade.clone()))
        })();
        match result {
            Ok(textures) => Some(textures),
            Err(error) => {
                field.take();
                FAILED_FIELD.set(Some(revision));
                tracing::info!(?error, "Rain field unavailable; using the runoff shader");
                None
            }
        }
    })
}

impl Field {
    #[allow(clippy::too_many_arguments)]
    fn render(
        &mut self,
        frame: &mut GlesFrame<'_, '_>,
        programs: &FieldPrograms,
        source: &GlesTexture,
        src: Rectangle<f64, Buffer>,
        dst: Rectangle<i32, Physical>,
        atlas: &GlesTexture,
        uniforms: &[Uniform<'_>],
    ) -> Result<(), GlesError> {
        let mut guard = frame.renderer();
        let renderer = guard.as_mut();
        let mut target = renderer.bind(&mut self.geometry)?;
        let mut pass = renderer.render(&mut target, self.key.output, self.key.transform)?;
        let (ready, active, previous) = pass.with_context(|gl| unsafe {
            gl.FramebufferTexture2D(
                ffi::FRAMEBUFFER,
                ffi::COLOR_ATTACHMENT1,
                ffi::TEXTURE_2D,
                self.shade.tex_id(),
                0,
            );
            gl.DrawBuffers(2, [ffi::COLOR_ATTACHMENT0, ffi::COLOR_ATTACHMENT1].as_ptr());
            let ready = gl.CheckFramebufferStatus(ffi::FRAMEBUFFER) == ffi::FRAMEBUFFER_COMPLETE;
            let (mut active, mut previous) = (0, 0);
            gl.GetIntegerv(ffi::ACTIVE_TEXTURE, &mut active);
            gl.ActiveTexture(ffi::TEXTURE2);
            gl.GetIntegerv(ffi::TEXTURE_BINDING_2D, &mut previous);
            gl.BindTexture(ffi::TEXTURE_2D, atlas.tex_id());
            gl.ActiveTexture(active as u32);
            (ready, active, previous)
        })?;
        let region = [Rectangle::from_size(dst.size)];
        let result = if ready {
            pass.render_texture_from_to(
                source,
                src,
                dst,
                &region,
                &region,
                Transform::Normal,
                1.0,
                Some(&programs.generate),
                uniforms,
            )
        } else {
            Err(GlesError::UnknownPixelFormat)
        };
        pass.with_context(|gl| unsafe {
            gl.DrawBuffers(1, [ffi::COLOR_ATTACHMENT0].as_ptr());
            gl.FramebufferTexture2D(
                ffi::FRAMEBUFFER,
                ffi::COLOR_ATTACHMENT1,
                ffi::TEXTURE_2D,
                0,
                0,
            );
            gl.ActiveTexture(ffi::TEXTURE2);
            gl.BindTexture(ffi::TEXTURE_2D, previous as u32);
            gl.ActiveTexture(active as u32);
        })?;
        result?;
        let _sync = pass.finish()?;
        Ok(())
    }
}
