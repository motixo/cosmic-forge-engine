// SPDX-License-Identifier: GPL-3.0-only
use smithay::{
    backend::renderer::{
        element::{Id, RenderElementStates},
        gles::Uniform,
    },
    output::Output,
};
use std::{
    cell::RefCell,
    collections::{HashMap, HashSet},
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    time::Instant,
};

#[derive(Default)]
struct Timing {
    previous: Option<Instant>,
    index: i32,
    motions: HashMap<Id, Motion>,
}

struct Motion {
    position: [f32; 2],
    velocity: [f32; 2],
    sampled: Instant,
    index: i32,
}

#[derive(Debug)]
pub struct Age {
    started: Instant,
    effect: Option<String>,
}

impl Default for Age {
    fn default() -> Self {
        Self {
            started: Instant::now(),
            effect: None,
        }
    }
}

impl Age {
    pub fn seconds(&mut self, effect: &str, now: Instant) -> f32 {
        if self.effect.as_deref() != Some(effect) {
            self.effect = Some(effect.into());
            self.started = now;
        }
        now.saturating_duration_since(self.started).as_secs_f32()
    }
}

impl Motion {
    fn sample(&mut self, position: [f32; 2], now: Instant, index: i32) {
        if self.index == index {
            return;
        }
        let elapsed = now.duration_since(self.sampled).as_secs_f32();
        self.velocity = if self.index == index.wrapping_sub(1) && elapsed > 0.0 && elapsed <= 0.25 {
            [
                (position[0] - self.position[0]) / elapsed,
                (position[1] - self.position[1]) / elapsed,
            ]
        } else {
            [0.0; 2]
        };
        self.position = position;
        self.sampled = now;
        self.index = index;
    }
}

#[derive(Default)]
struct OutputState {
    visible: AtomicBool,
    timing: Mutex<Timing>,
}

#[derive(Default)]
struct Frame {
    output: Option<Output>,
    now: Option<Instant>,
    time: f32,
    delta: f32,
    index: i32,
    elements: HashSet<Id>,
    visible: bool,
}

thread_local! {
    static FRAME: RefCell<Frame> = RefCell::new(Frame::default());
}

fn output_state(output: &Output) -> &OutputState {
    output
        .user_data()
        .insert_if_missing_threadsafe(OutputState::default);
    output.user_data().get().unwrap()
}

pub fn begin_frame(output: &Output) {
    static START: OnceLock<Instant> = OnceLock::new();
    let now = Instant::now();
    let time = now.duration_since(*START.get_or_init(|| now)).as_secs_f32();
    let mut timing = output_state(output).timing.lock().unwrap();
    let previous_index = timing.index.wrapping_sub(1);
    timing
        .motions
        .retain(|_, motion| motion.index == previous_index);
    let delta = timing.previous.replace(now).map_or(0.0, |previous| {
        now.duration_since(previous).as_secs_f32().min(0.25)
    });
    FRAME.with_borrow_mut(|frame| {
        frame.output = Some(output.clone());
        frame.now = Some(now);
        frame.time = time;
        frame.delta = delta;
        frame.index = timing.index;
        frame.elements.clear();
        frame.visible = false;
    });
    timing.index = timing.index.wrapping_add(1);
}

pub fn output_size() -> [f32; 2] {
    FRAME.with_borrow(|frame| {
        frame
            .output
            .as_ref()
            .and_then(|output| {
                output.current_mode().map(|mode| {
                    let size = output
                        .current_transform()
                        .transform_size(mode.size)
                        .to_f64()
                        .to_logical(output.current_scale().fractional_scale());
                    [size.w as f32, size.h as f32]
                })
            })
            .unwrap_or([0.0; 2])
    })
}

pub fn uniforms(id: &Id, animated: bool, location: [f32; 2]) -> [Uniform<'static>; 5] {
    FRAME.with_borrow_mut(|frame| {
        let mut position = [0.0; 2];
        let mut velocity = [0.0; 2];
        let (time, delta, index) = if animated {
            frame.elements.insert(id.clone());
            if let (Some(output), Some(now)) = (&frame.output, frame.now) {
                let origin = output.current_location();
                position = [location[0] + origin.x as f32, location[1] + origin.y as f32];
                let mut timing = output_state(output).timing.lock().unwrap();
                let motion = timing.motions.entry(id.clone()).or_insert(Motion {
                    position,
                    velocity,
                    sampled: now,
                    index: frame.index,
                });
                motion.sample(position, now, frame.index);
                velocity = motion.velocity;
            }
            (frame.time, frame.delta, frame.index)
        } else {
            (0.0, 0.0, 0)
        };
        [
            Uniform::new("time", time),
            Uniform::new("delta_time", delta),
            Uniform::new("frame_index", index),
            Uniform::new("window_position", position),
            Uniform::new("window_velocity", velocity),
        ]
    })
}

pub fn observe(states: &RenderElementStates) {
    FRAME.with_borrow_mut(|frame| {
        frame.visible |= frame.elements.iter().any(|id| {
            states
                .element_render_state(id.clone())
                .is_some_and(|state| state.visible_area > 0)
        });
    });
}

pub fn finish_frame(output: &Output, states: &RenderElementStates) {
    observe(states);
    let visible = FRAME.with_borrow(|frame| frame.visible);
    output_state(output)
        .visible
        .store(visible, Ordering::Relaxed);
}

pub fn active(output: &Output) -> bool {
    output
        .user_data()
        .get::<OutputState>()
        .is_some_and(|state| state.visible.load(Ordering::Relaxed))
}

#[cfg(test)]
mod tests {
    use super::*;
    use smithay::{
        backend::renderer::{
            element::{RenderElementPresentationState, RenderElementState},
            gles::UniformValue,
        },
        output::{PhysicalProperties, Subpixel},
    };
    use std::time::Duration;

    #[test]
    fn output_dimensions_follow_the_rendering_monitor_scale_rotation_and_mode() {
        use smithay::{
            output::{Mode, Scale},
            utils::Transform,
        };
        let first = output("first");
        let second = output("second");
        first.change_current_state(
            Some(Mode {
                size: (1920, 1080).into(),
                refresh: 60000,
            }),
            Some(Transform::Normal),
            Some(Scale::Integer(1)),
            Some((-1920, 80).into()),
        );
        second.change_current_state(
            Some(Mode {
                size: (2560, 1440).into(),
                refresh: 60000,
            }),
            Some(Transform::_90),
            Some(Scale::Fractional(1.5)),
            Some((0, 0).into()),
        );
        let id = Id::new();
        begin_frame(&first);
        uniforms(&id, false, [10.0; 2]);
        assert_eq!(output_size(), [1920.0, 1080.0]);
        assert!(!active(&first));
        begin_frame(&second);
        uniforms(&id, false, [20.0; 2]);
        let size = output_size();
        assert_eq!(size[0], 960.0);
        assert!((size[1] - 2560.0 / 1.5).abs() < 0.001);
        assert!(!active(&second));
        begin_frame(&first);
        assert_eq!(output_size(), [1920.0, 1080.0]);
        first.change_current_state(
            Some(Mode {
                size: (3840, 2160).into(),
                refresh: 60000,
            }),
            None,
            Some(Scale::Integer(2)),
            None,
        );
        assert_eq!(output_size(), [1920.0, 1080.0]);
        first.change_current_state(None, Some(Transform::_270), None, None);
        assert_eq!(output_size(), [1080.0, 1920.0]);
        FRAME.with_borrow_mut(|frame| frame.output = None);
        assert_eq!(output_size(), [0.0; 2]);
    }

    fn output(name: &str) -> Output {
        Output::new(
            name.into(),
            PhysicalProperties {
                size: (0, 0).into(),
                subpixel: Subpixel::Unknown,
                make: "test".into(),
                model: "test".into(),
                serial_number: "test".into(),
            },
        )
    }

    #[test]
    fn timing_is_scoped_to_each_output_and_static_uniforms_are_zero() {
        let first = output("first");
        let second = output("second");
        begin_frame(&first);
        FRAME.with_borrow(|frame| assert_eq!((frame.delta, frame.index), (0.0, 0)));
        output_state(&first).timing.lock().unwrap().previous =
            Some(Instant::now() - Duration::from_secs(5));
        begin_frame(&first);
        FRAME.with_borrow(|frame| assert_eq!((frame.delta, frame.index), (0.25, 1)));
        let static_values = uniforms(&Id::new(), false, [80.0, 90.0]);
        assert!(matches!(static_values[0].value, UniformValue::_1f(0.0)));
        assert!(matches!(static_values[1].value, UniformValue::_1f(0.0)));
        assert!(matches!(static_values[2].value, UniformValue::_1i(0)));
        assert!(matches!(
            static_values[3].value,
            UniformValue::_2f(0.0, 0.0)
        ));
        assert!(matches!(
            static_values[4].value,
            UniformValue::_2f(0.0, 0.0)
        ));
        begin_frame(&second);
        FRAME.with_borrow(|frame| assert_eq!((frame.delta, frame.index), (0.0, 0)));
    }

    #[test]
    fn window_age_starts_independently_and_survives_gaps_until_the_effect_changes() {
        let now = Instant::now();
        let mut first = Age::default();
        let mut second = Age::default();
        assert_eq!(first.seconds("snow", now), 0.0);
        assert_eq!(first.seconds("snow", now + Duration::from_secs(10)), 10.0);
        assert_eq!(second.seconds("snow", now + Duration::from_secs(10)), 0.0);
        assert_eq!(first.seconds("snow", now + Duration::from_secs(120)), 120.0);
        assert_eq!(
            second.seconds("snow", now + Duration::from_secs(120)),
            110.0
        );
        assert_eq!(first.seconds("ember", now + Duration::from_secs(121)), 0.0);
        assert_eq!(first.seconds("snow", now + Duration::from_secs(122)), 0.0);
        assert_eq!(
            second.seconds("snow", now + Duration::from_secs(122)),
            112.0
        );
    }

    #[test]
    fn only_visible_animated_elements_keep_their_output_active() {
        let first = output("first");
        let second = output("second");
        let id = Id::new();
        let mut states = RenderElementStates::default();
        states.states.insert(
            id.clone(),
            RenderElementState {
                visible_area: 8,
                presentation_state: RenderElementPresentationState::Rendering { reason: None },
                needs_capture: false,
            },
        );
        begin_frame(&first);
        uniforms(&id, true, [0.0; 2]);
        finish_frame(&first, &states);
        assert!(active(&first));
        assert!(!active(&second));
        begin_frame(&first);
        uniforms(&id, false, [0.0; 2]);
        finish_frame(&first, &states);
        assert!(!active(&first));
        begin_frame(&first);
        uniforms(&id, true, [0.0; 2]);
        states.states.get_mut(&id).unwrap().visible_area = 0;
        finish_frame(&first, &states);
        assert!(!active(&first));
        begin_frame(&first);
        uniforms(&id, true, [0.0; 2]);
        finish_frame(&first, &RenderElementStates::default());
        assert!(!active(&first));
    }

    #[test]
    fn motion_uses_logical_desktop_coordinates_and_resets_after_a_pause() {
        let first = output("first");
        first.change_current_state(None, None, None, Some((1280, -100).into()));
        let id = Id::new();
        begin_frame(&first);
        let values = uniforms(&id, true, [20.0, 40.0]);
        assert!(matches!(values[3].value, UniformValue::_2f(1300.0, -60.0)));
        assert!(matches!(values[4].value, UniformValue::_2f(0.0, 0.0)));
        let second = output("second");
        begin_frame(&second);
        let values = uniforms(&id, true, [200.0, 300.0]);
        assert!(matches!(values[4].value, UniformValue::_2f(0.0, 0.0)));

        let now = Instant::now();
        let mut motion = Motion {
            position: [100.0, 80.0],
            velocity: [0.0; 2],
            sampled: now,
            index: 0,
        };
        motion.sample([110.0, 60.0], now + Duration::from_millis(100), 1);
        assert_eq!(motion.velocity, [100.0, -200.0]);
        motion.sample([110.0, 60.0], now + Duration::from_millis(100), 1);
        assert_eq!(motion.velocity, [100.0, -200.0]);
        motion.sample([110.0, 60.0], now + Duration::from_millis(200), 2);
        assert_eq!(motion.velocity, [0.0; 2]);
        motion.sample([400.0, 800.0], now + Duration::from_secs(2), 3);
        assert_eq!(motion.velocity, [0.0; 2]);
        motion.sample([500.0, 900.0], now + Duration::from_millis(2100), 5);
        assert_eq!(motion.velocity, [0.0; 2]);
        begin_frame(&first);
        begin_frame(&first);
        assert!(
            output_state(&first)
                .timing
                .lock()
                .unwrap()
                .motions
                .is_empty()
        );
    }
}
