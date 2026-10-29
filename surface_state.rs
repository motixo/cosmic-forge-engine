// SPDX-License-Identifier: GPL-3.0-only
use smithay::{
    reexports::wayland_server::{Resource, Weak, protocol::wl_surface::WlSurface},
    wayland::{
        compositor::{get_parent, with_states},
        shell::xdg::{XdgPopupSurfaceData, XdgToplevelSurfaceData},
    },
};
use std::{
    collections::HashSet,
    sync::{
        Mutex,
        atomic::{AtomicBool, Ordering},
    },
};

static DARK: AtomicBool = AtomicBool::new(true);
static FOCUS_CHANGED: AtomicBool = AtomicBool::new(false);
struct Active(AtomicBool);
struct Parent(Mutex<Option<Weak<WlSurface>>>);

pub fn set_dark(dark: bool) {
    DARK.store(dark, Ordering::Relaxed);
}

pub fn dark() -> bool {
    DARK.load(Ordering::Relaxed)
}

pub fn set_active(surface: &WlSurface, active: bool) {
    with_states(surface, |states| {
        let previous = states
            .data_map
            .get_or_insert_threadsafe(|| Active(AtomicBool::new(!active)))
            .0
            .swap(active, Ordering::Relaxed);
        if previous != active {
            FOCUS_CHANGED.store(true, Ordering::Relaxed);
        }
    });
}

pub fn take_focus_change() -> bool {
    FOCUS_CHANGED.swap(false, Ordering::Relaxed)
}

pub fn set_parent(surface: &WlSurface, parent: Option<&WlSurface>) {
    with_states(surface, |states| {
        let mut value = states
            .data_map
            .get_or_insert_threadsafe(|| Parent(Mutex::new(None)))
            .0
            .lock()
            .unwrap();
        if value.as_ref().and_then(|v| v.upgrade().ok()).as_ref() != parent {
            *value = parent.map(Resource::downgrade);
            FOCUS_CHANGED.store(true, Ordering::Relaxed);
        }
    });
}

pub fn active(surface: &WlSurface) -> bool {
    let mut surface = Some(surface.clone());
    let mut owned = false;
    let mut active = false;
    let mut seen = HashSet::new();
    while let Some(current) = surface {
        if !seen.insert(current.clone()) {
            break;
        }
        let (value, parent) = with_states(&current, |states| {
            let value = states
                .data_map
                .get::<Active>()
                .map(|v| v.0.load(Ordering::Relaxed))
                .or_else(|| {
                    states
                        .data_map
                        .get::<XdgToplevelSurfaceData>()
                        .map(|_| false)
                });
            let parent = states
                .data_map
                .get::<Parent>()
                .and_then(|data| {
                    data.0
                        .lock()
                        .unwrap()
                        .as_ref()
                        .and_then(|parent| parent.upgrade().ok())
                })
                .or_else(|| {
                    states
                        .data_map
                        .get::<XdgPopupSurfaceData>()
                        .and_then(|data| data.lock().unwrap().parent.clone())
                })
                .or_else(|| {
                    states
                        .data_map
                        .get::<XdgToplevelSurfaceData>()
                        .and_then(|data| data.lock().unwrap().parent.clone())
                });
            (value, parent)
        });
        if let Some(value) = value {
            owned = true;
            active = value;
        }
        surface = get_parent(&current).or(parent);
    }
    !owned || active
}
