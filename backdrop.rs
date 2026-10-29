// SPDX-License-Identifier: GPL-3.0-only
use super::{
    blur_effect::{BlurShaders, render_blur},
    forge_loader,
    forge_runtime::Backdrop,
};
use crate::backend::render::element::AsGlowRenderer;
use smithay::{
    backend::{
        allocator::Fourcc,
        renderer::{
            Bind, Color32F, Frame, Offscreen, Renderer, Texture,
            element::{
                Element, Id, Kind,
                surface::{WaylandSurfaceRenderElement, render_elements_from_surface_tree},
            },
            gles::{GlesError, GlesRenderer, GlesTexture, ffi},
            utils::{CommitCounter, draw_render_elements},
        },
    },
    desktop::layer_map_for_output,
    output::{Output, WeakOutput},
    utils::{Buffer, Physical, Rectangle, Size, Transform},
    wayland::shell::wlr_layer::Layer,
};
use std::{
    borrow::{Borrow, BorrowMut},
    cell::RefCell,
    collections::HashMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
};

#[derive(Clone)]
pub struct Image {
    pub sharp: GlesTexture,
    pub blurred: GlesTexture,
    pub revision: u64,
}

#[derive(PartialEq)]
struct Signature {
    elements: Vec<(
        Id,
        CommitCounter,
        Rectangle<f64, Buffer>,
        Rectangle<i32, Physical>,
        Transform,
    )>,
    size: Size<i32, Buffer>,
    scale: f64,
    transform: Transform,
}

struct Wallpaper {
    signature: Signature,
    sharp: GlesTexture,
    revision: u64,
    blur: Option<(f64, usize, Image)>,
}

type Cache = Arc<Mutex<HashMap<WeakOutput, Wallpaper>>>;
struct View {
    output: WeakOutput,
    backdrop: Backdrop,
}
thread_local! {
    static VIEW: RefCell<Option<View>> = const { RefCell::new(None) };
}

pub fn selected() -> Backdrop {
    VIEW.with_borrow(|view| view.as_ref().map_or(Backdrop::Scene, |view| view.backdrop))
}

fn revision() -> u64 {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    NEXT.fetch_add(1, Ordering::Relaxed)
}

fn clamp(renderer: &mut GlesRenderer, texture: &GlesTexture) -> Result<(), GlesError> {
    renderer.with_context(|gl| unsafe {
        let mut active = 0;
        let mut binding = 0;
        gl.GetIntegerv(ffi::ACTIVE_TEXTURE, &mut active);
        gl.ActiveTexture(ffi::TEXTURE0);
        gl.GetIntegerv(ffi::TEXTURE_BINDING_2D, &mut binding);
        gl.BindTexture(ffi::TEXTURE_2D, texture.tex_id());
        gl.TexParameteri(
            ffi::TEXTURE_2D,
            ffi::TEXTURE_WRAP_S,
            ffi::CLAMP_TO_EDGE as i32,
        );
        gl.TexParameteri(
            ffi::TEXTURE_2D,
            ffi::TEXTURE_WRAP_T,
            ffi::CLAMP_TO_EDGE as i32,
        );
        gl.BindTexture(ffi::TEXTURE_2D, binding as u32);
        gl.ActiveTexture(active as u32);
    })
}

pub fn prepare<R: AsGlowRenderer>(renderer: &mut R, output: &Output) -> Result<(), R::Error>
where
    R::TextureId: Clone + 'static,
{
    let backdrop = forge_loader::backdrop();
    // All surfaces in this render use the source prepared for this output frame.
    VIEW.with_borrow_mut(|current| {
        *current = Some(View {
            output: output.downgrade(),
            backdrop,
        })
    });
    if backdrop != Backdrop::Wallpaper {
        return Ok(());
    }
    let Some(mode) = output.current_mode() else {
        return Ok(());
    };
    let scale = output.current_scale().fractional_scale();
    let physical = output.current_transform().transform_size(mode.size);
    let size = physical.to_logical(1).to_buffer(1, Transform::Normal);
    let layers = {
        let map = layer_map_for_output(output);
        map.layers_on(Layer::Background)
            .filter(|layer| layer.namespace() == "wallpaper")
            .filter_map(|layer| {
                map.layer_geometry(layer)
                    .map(|geo| (layer.clone(), geo.loc))
            })
            .collect::<Vec<_>>()
    };
    let mut elements: Vec<WaylandSurfaceRenderElement<R>> = Vec::new();
    for (layer, location) in layers.into_iter().rev() {
        elements.extend(render_elements_from_surface_tree(
            renderer,
            layer.wl_surface(),
            location.to_physical_precise_round(scale),
            scale,
            1.0,
            Kind::Unspecified,
        ));
    }
    let signature = Signature {
        elements: elements
            .iter()
            .map(|e| {
                (
                    e.id().clone(),
                    e.current_commit(),
                    e.src(),
                    e.geometry(scale.into()),
                    e.transform(),
                )
            })
            .collect(),
        size,
        scale,
        transform: output.current_transform(),
    };
    let cache = {
        let data = Borrow::<GlesRenderer>::borrow(renderer.glow_renderer())
            .egl_context()
            .user_data();
        data.insert_if_missing(|| Arc::new(Mutex::new(HashMap::<WeakOutput, Wallpaper>::new())));
        data.get::<Cache>().unwrap().clone()
    };
    let mut cache = cache.lock().unwrap();
    cache.retain(|output, _| output.upgrade().is_some());
    let key = output.downgrade();
    if cache
        .get(&key)
        .is_some_and(|wallpaper| wallpaper.signature == signature)
    {
        return Ok(());
    }
    let mut sharp = renderer.create_buffer(Fourcc::Abgr8888, size)?;
    let mut fb = renderer.bind(&mut sharp)?;
    let mut frame = renderer.render(&mut fb, physical, Transform::Normal)?;
    let damage = [Rectangle::from_size(physical)];
    frame.clear(Color32F::BLACK, &damage)?;
    draw_render_elements::<R, _, _>(&mut frame, scale, &elements, &damage)?;
    let sync = frame.finish()?;
    drop(fb);
    renderer.wait(&sync)?;
    clamp(
        BorrowMut::<GlesRenderer>::borrow_mut(renderer.glow_renderer_mut()),
        &sharp,
    )
    .map_err(R::from_gles_error)?;
    cache.insert(
        key,
        Wallpaper {
            signature,
            sharp,
            revision: revision(),
            blur: None,
        },
    );
    Ok(())
}

pub fn current<R: AsGlowRenderer>(
    renderer: &mut R,
    offset: f64,
    passes: usize,
) -> Result<Option<Image>, R::Error> {
    let Some(output) = VIEW.with_borrow(|view| view.as_ref().map(|view| view.output.clone()))
    else {
        return Ok(None);
    };
    let shaders = BlurShaders::get(renderer);
    let gles = BorrowMut::<GlesRenderer>::borrow_mut(renderer.glow_renderer_mut());
    let data = gles.egl_context().user_data();
    let Some(cache) = data.get::<Cache>().cloned() else {
        return Ok(None);
    };
    let mut cache = cache.lock().unwrap();
    let Some(wallpaper) = cache.get_mut(&output) else {
        return Ok(None);
    };
    if passes == 0 {
        return Ok(Some(Image {
            sharp: wallpaper.sharp.clone(),
            blurred: wallpaper.sharp.clone(),
            revision: wallpaper.revision,
        }));
    }
    if let Some((old_offset, old_passes, image)) = &wallpaper.blur {
        if *old_offset == offset && *old_passes == passes {
            return Ok(Some(image.clone()));
        }
    }
    let size = wallpaper.sharp.size();
    let area = Rectangle::from_size(size.to_logical(1, Transform::Normal).to_physical(1));
    let mut blurred = gles
        .create_buffer(Fourcc::Abgr8888, size)
        .map_err(R::from_gles_error)?;
    let mut scratch = gles
        .create_buffer(Fourcc::Abgr8888, size)
        .map_err(R::from_gles_error)?;
    let mut fb = gles.bind(&mut blurred).map_err(R::from_gles_error)?;
    let mut frame = gles
        .render(&mut fb, area.size, Transform::Normal)
        .map_err(R::from_gles_error)?;
    frame
        .clear(Color32F::TRANSPARENT, &[area])
        .map_err(R::from_gles_error)?;
    frame
        .render_texture_from_to(
            &wallpaper.sharp,
            Rectangle::from_size(size.to_f64()),
            area,
            &[area],
            &[],
            Transform::Normal,
            1.0,
            None,
            &[],
        )
        .map_err(R::from_gles_error)?;
    let sync = frame.finish().map_err(R::from_gles_error)?;
    drop(fb);
    gles.wait(&sync).map_err(R::from_gles_error)?;
    render_blur(
        gles,
        &shaders,
        &mut [&mut blurred, &mut scratch],
        offset,
        passes,
    )
    .map_err(R::from_gles_error)?;
    clamp(gles, &blurred).map_err(R::from_gles_error)?;
    let image = Image {
        sharp: wallpaper.sharp.clone(),
        blurred,
        revision: revision(),
    };
    wallpaper.blur = Some((offset, passes, image.clone()));
    Ok(Some(image))
}

#[cfg(test)]
mod tests {
    use super::*;
    use cosmic_config::{Config, ConfigSet};
    use smithay::{
        backend::{
            egl::{EGLContext, EGLDisplay, native::EGLSurfacelessDisplay},
            renderer::{ExportMem, glow::GlowRenderer},
        },
        output::{Mode, PhysicalProperties, Scale, Subpixel},
    };
    use std::path::PathBuf;

    fn output(name: &str, size: (i32, i32), scale: f64, transform: Transform) -> Output {
        let output = Output::new(
            name.into(),
            PhysicalProperties {
                size: (0, 0).into(),
                subpixel: Subpixel::Unknown,
                make: "test".into(),
                model: "test".into(),
                serial_number: name.into(),
            },
        );
        output.change_current_state(
            Some(Mode {
                size: size.into(),
                refresh: 60000,
            }),
            Some(transform),
            Some(Scale::Fractional(scale)),
            Some((0, 0).into()),
        );
        output
    }

    fn paint(renderer: &mut GlowRenderer, image: &Image, color: Color32F) {
        let mut texture = image.sharp.clone();
        let area = Rectangle::from_size(
            texture
                .size()
                .to_logical(1, Transform::Normal)
                .to_physical(1),
        );
        let mut target = renderer.bind(&mut texture).unwrap();
        let mut frame = renderer
            .render(&mut target, area.size, Transform::Normal)
            .unwrap();
        frame.clear(color, &[area]).unwrap();
        let sync = frame.finish().unwrap();
        drop(target);
        renderer.wait(&sync).unwrap();
    }

    fn pixel(renderer: &mut GlowRenderer, image: &Image) -> Vec<u8> {
        pixel_at(renderer, &image.blurred, (10, 10))
    }

    fn pixel_at(renderer: &mut GlowRenderer, texture: &GlesTexture, point: (i32, i32)) -> Vec<u8> {
        let mapping = renderer
            .copy_texture(
                texture,
                Rectangle::new(point.into(), (1, 1).into()),
                Fourcc::Abgr8888,
            )
            .unwrap();
        renderer.map_texture(&mapping).unwrap().to_vec()
    }

    #[test]
    #[ignore = "requires surfaceless EGL and private FORGE_QA_ROOT/XDG_RUNTIME_DIR"]
    fn wallpaper_cache_isolates_outputs_and_reuses_images_and_blur() {
        let root = PathBuf::from(std::env::var_os("FORGE_QA_ROOT").expect("Set a private QA root"));
        let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap());
        assert!(root.is_absolute() && runtime.starts_with(&root));
        std::fs::create_dir_all(&runtime).unwrap();
        let config =
            Config::with_custom_path("com.system76.CosmicComp", 1, root.join("config")).unwrap();
        config.set("forge_backdrop", Backdrop::Wallpaper).unwrap();
        forge_loader::configure(&config);
        let display = unsafe { EGLDisplay::new(EGLSurfacelessDisplay).unwrap() };
        let mut renderer =
            unsafe { GlowRenderer::new(EGLContext::new(&display).unwrap()).unwrap() };
        crate::backend::render::init_shaders(renderer.borrow_mut()).unwrap();
        let first = output("first", (192, 128), 1.0, Transform::Normal);
        let second = output("second", (320, 192), 1.5, Transform::_90);
        prepare(&mut renderer, &first).unwrap();
        let image = current(&mut renderer, 1.0, 0).unwrap().unwrap();
        paint(&mut renderer, &image, Color32F::new(1.0, 0.0, 0.0, 1.0));
        let red = current(&mut renderer, 1.0, 1).unwrap().unwrap();
        assert_eq!(red.sharp.size(), (192, 128).into());
        let value = pixel(&mut renderer, &red);
        assert!(value[0] > 200 && value[2] < 5);

        prepare(&mut renderer, &second).unwrap();
        let image = current(&mut renderer, 1.0, 0).unwrap().unwrap();
        paint(&mut renderer, &image, Color32F::new(0.0, 0.0, 1.0, 1.0));
        let blue = current(&mut renderer, 1.0, 1).unwrap().unwrap();
        assert_eq!(blue.sharp.size(), (192, 320).into());
        assert_ne!(red.revision, blue.revision);
        let value = pixel(&mut renderer, &blue);
        assert!(value[2] > 200 && value[0] < 5);

        first.change_current_state(None, None, None, Some((-192, 50).into()));
        prepare(&mut renderer, &first).unwrap();
        let reused = current(&mut renderer, 1.0, 1).unwrap().unwrap();
        assert_eq!(red.revision, reused.revision);
        assert_eq!(red.sharp.tex_id(), reused.sharp.tex_id());
        assert_eq!(red.blurred.tex_id(), reused.blurred.tex_id());
        assert!(pixel(&mut renderer, &reused)[0] > 200);
        let changed_blur = current(&mut renderer, 2.0, 1).unwrap().unwrap();
        assert_ne!(red.revision, changed_blur.revision);
        first.change_current_state(
            None,
            Some(Transform::_180),
            Some(Scale::Fractional(1.25)),
            None,
        );
        prepare(&mut renderer, &first).unwrap();
        let changed_output = current(&mut renderer, 1.0, 1).unwrap().unwrap();
        assert_ne!(red.revision, changed_output.revision);
        assert!(pixel(&mut renderer, &changed_output)[0] < 5);

        first.change_current_state(None, Some(Transform::Normal), Some(Scale::Integer(1)), None);
        prepare(&mut renderer, &first).unwrap();
        let image = current(&mut renderer, 1.0, 0).unwrap().unwrap();
        paint(&mut renderer, &image, Color32F::new(1.0, 0.0, 0.0, 1.0));
        for base in ["glass", "aero", "crystal"] {
            let effect = super::super::forge_runtime::RuntimeEffect {
                abi: 1,
                id: "gpu-test".into(),
                base: base.into(),
                source: include_str!("../shaders/forge_glass.frag").into(),
                animated: false,
                backdrop: Backdrop::Wallpaper,
                parameters: vec![],
            };
            config.set("glass_effect", "gpu-test").unwrap();
            config.set("forge_runtime", effect).unwrap();
            forge_loader::configure(&config);
            let mut state = super::super::blur_effect::BlurState::default();
            let element = super::super::blur_effect::BlurElement::from_state(
                &mut renderer,
                &mut state,
                true,
                Rectangle::new((16.0, 16.0).into(), (100.0, 60.0).into()),
                1.0,
                [12; 4],
                1,
            )
            .unwrap()
            .unwrap();
            assert!(!element.is_framebuffer_effect());
            let mut target: GlesTexture = renderer
                .create_buffer(Fourcc::Abgr8888, (192, 128).into())
                .unwrap();
            let mut fb = renderer.bind(&mut target).unwrap();
            let mut frame = renderer
                .render(&mut fb, (192, 128).into(), Transform::Normal)
                .unwrap();
            frame
                .clear(
                    Color32F::new(0.0, 0.0, 1.0, 1.0),
                    &[Rectangle::from_size((192, 128).into())],
                )
                .unwrap();
            let geometry = element.geometry(1.0.into());
            smithay::backend::renderer::element::RenderElement::<GlowRenderer>::draw(
                &element,
                &mut frame,
                element.src(),
                geometry,
                &[Rectangle::from_size(geometry.size)],
                &[],
                None,
            )
            .unwrap();
            let sync = frame.finish().unwrap();
            drop(fb);
            renderer.wait(&sync).unwrap();
            assert!(pixel_at(&mut renderer, &target, (50, 40))[0] > 200);
            assert!(pixel_at(&mut renderer, &target, (16, 16))[2] > 200);
        }
    }
}
