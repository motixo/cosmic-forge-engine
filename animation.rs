// SPDX-License-Identifier: GPL-3.0-only
use smithay::{
    backend::renderer::{
        element::{Element, Id, NamespacedElement, RenderElementStates},
        gles::Uniform,
    },
    output::Output,
    reexports::calloop::{
        LoopHandle,
        timer::{TimeoutAction, Timer},
    },
};
use std::{
    cell::RefCell,
    collections::{HashMap, HashSet},
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, AtomicU16, Ordering},
    },
    time::{Duration, Instant},
};

pub const CLOCK_UNIFORM: &str = "forge_animation_clock";

static FPS: AtomicU16 = AtomicU16::new(0);

pub fn set_fps(fps: u16) {
    FPS.store(fps, Ordering::Relaxed);
}

fn interval(fps: u16, refresh: Option<i32>) -> Option<Duration> {
    (fps != 0 && refresh.is_none_or(|refresh| refresh > i32::from(fps) * 1000))
        .then(|| Duration::from_secs(1) / u32::from(fps))
}

#[derive(Default)]
struct Cadence {
    interval: Option<Duration>,
    sampled: Option<Instant>,
    deadline: Option<Instant>,
    index: i32,
    delta: f32,
}

impl Cadence {
    fn sample(&mut self, now: Instant, interval: Option<Duration>, visible: bool) -> bool {
        if !visible || self.interval != interval {
            self.interval = interval;
            self.deadline = None;
        }
        if self.deadline.is_some_and(|deadline| now < deadline) {
            return false;
        }
        self.deadline = interval.map(|interval| {
            let remainder = self.deadline.map_or(0, |deadline| {
                now.duration_since(deadline).as_nanos() % interval.as_nanos()
            });
            now + interval - Duration::from_nanos(remainder as u64)
        });
        if let Some(previous) = self.sampled.replace(now) {
            self.delta = now.duration_since(previous).as_secs_f32().min(0.25);
            self.index = self.index.wrapping_add(1);
        }
        true
    }
}

#[derive(Clone, Copy)]
pub struct Clock {
    anchor: Instant,
    phase: f64,
    rate: f64,
}

impl Clock {
    pub fn new(now: Instant, rate: f64) -> Self {
        Self {
            anchor: now,
            phase: 0.0,
            rate,
        }
    }

    fn seconds(self, now: Instant) -> f64 {
        self.phase + now.saturating_duration_since(self.anchor).as_secs_f64() * self.rate
    }

    pub fn set_rate(&mut self, now: Instant, rate: f64) {
        if self.rate != rate {
            self.phase = self.seconds(now);
            self.anchor = now;
            self.rate = rate;
        }
    }

    pub fn uniform(self, animated: bool, focus: Option<FocusSample>) -> Uniform<'static> {
        let seconds = FRAME.with_borrow(|frame| {
            if animated {
                focus.and_then(|sample| sample.clock).map_or_else(
                    || frame.sampled.map_or(0.0, |now| self.seconds(now) as f32),
                    |seconds| seconds as f32,
                )
            } else {
                0.0
            }
        });
        Uniform::new(CLOCK_UNIFORM, (seconds, 1.0_f32))
    }
}

#[derive(Clone, Copy, Debug)]
pub struct FocusSample {
    pub time: Instant,
    pub previous: Option<Instant>,
    index: i32,
    delta: f32,
    clock: Option<f64>,
}

#[derive(Debug)]
pub struct FocusClock {
    effect: String,
    sampled: Instant,
    active: bool,
    phase: FocusSample,
}

impl FocusClock {
    pub fn sample(
        state: &mut Option<Self>,
        effect: &str,
        active: bool,
        clock: Option<Clock>,
    ) -> FocusSample {
        FRAME.with_borrow(|frame| {
            let sampled = frame.sampled.unwrap_or_else(Instant::now);
            if state.as_ref().is_none_or(|state| state.effect != effect) {
                *state = Some(Self {
                    effect: effect.into(),
                    sampled,
                    active,
                    phase: FocusSample {
                        time: sampled,
                        previous: None,
                        index: frame.animation_index,
                        delta: frame.delta,
                        clock: clock.map(|clock| clock.seconds(sampled)),
                    },
                });
            }
            let state = state.as_mut().unwrap();
            state.advance(sampled, frame.previous_sampled, active, clock);
            state.phase
        })
    }

    fn advance(
        &mut self,
        sampled: Instant,
        previous: Option<Instant>,
        active: bool,
        clock: Option<Clock>,
    ) {
        // The surface may also have sampled a newer tick on another output.
        let elapsed = if active && self.active && previous.is_some_and(|tick| tick <= self.sampled)
        {
            sampled.saturating_duration_since(self.sampled)
        } else {
            Duration::ZERO
        };
        if !elapsed.is_zero() {
            self.phase.previous = Some(self.phase.time);
            self.phase.time += elapsed;
            self.phase.delta = elapsed.as_secs_f32().min(0.25);
            self.phase.index = self.phase.index.wrapping_add(1);
        }
        self.phase.clock = clock.map(|clock| {
            self.phase.clock.unwrap_or_else(|| clock.seconds(sampled))
                + elapsed.as_secs_f64() * clock.rate
        });
        self.sampled = self.sampled.max(sampled);
        self.active = active;
    }
}

#[derive(Default)]
struct Timing {
    index: i32,
    motions: HashMap<Id, Motion>,
    cadence: Cadence,
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
        self.elapsed(now)
    }

    pub fn elapsed(&self, now: Instant) -> f32 {
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
    sampled: Option<Instant>,
    previous_sampled: Option<Instant>,
    animation_step: bool,
    capped: bool,
    started: Option<Instant>,
    delta: f32,
    index: i32,
    animation_index: i32,
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
    begin_frame_with_refresh(output, None);
}

pub fn begin_vblank_frame(output: &Output) {
    begin_frame_with_refresh(output, output.current_mode().map(|mode| mode.refresh));
}

fn begin_frame_with_refresh(output: &Output, refresh: Option<i32>) {
    static START: OnceLock<Instant> = OnceLock::new();
    let now = Instant::now();
    super::forge_effect_map::expire(now);
    let start = *START.get_or_init(|| now);
    let state = output_state(output);
    let mut timing = state.timing.lock().unwrap();
    let previous_index = timing.index.wrapping_sub(1);
    timing
        .motions
        .retain(|_, motion| motion.index == previous_index);
    let previous_sampled = timing.cadence.sampled;
    let animation_step = timing.cadence.sample(
        now,
        interval(FPS.load(Ordering::Relaxed), refresh),
        state.visible.load(Ordering::Relaxed),
    );
    let sampled = timing.cadence.sampled.unwrap_or(now);
    FRAME.with_borrow_mut(|frame| {
        frame.output = Some(output.clone());
        frame.now = Some(now);
        frame.sampled = Some(sampled);
        frame.previous_sampled = previous_sampled;
        frame.animation_step = animation_step;
        frame.capped = timing.cadence.interval.is_some();
        frame.started = Some(start);
        frame.delta = timing.cadence.delta;
        frame.index = timing.index;
        frame.animation_index = timing.cadence.index;
        frame.elements.clear();
        frame.visible = false;
    });
    timing.index = timing.index.wrapping_add(1);
}

pub fn sample_time() -> Instant {
    FRAME.with_borrow(|frame| frame.sampled.unwrap_or_else(Instant::now))
}

pub fn animation_step() -> bool {
    FRAME.with_borrow(|frame| frame.animation_step)
}

pub fn previous_sample_time() -> Option<Instant> {
    FRAME.with_borrow(|frame| frame.previous_sampled)
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

pub fn uniforms(
    id: &Id,
    animated: bool,
    location: [f32; 2],
    focus: Option<FocusSample>,
) -> [Uniform<'static>; 5] {
    FRAME.with_borrow_mut(|frame| {
        let mut position = [0.0; 2];
        let mut velocity = [0.0; 2];
        let (time, delta, index) = if animated {
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
            let sample = focus.map(|sample| sample.time).or(frame.sampled);
            let time = sample.zip(frame.started).map_or(0.0, |(sample, start)| {
                sample.saturating_duration_since(start).as_secs_f32()
            });
            focus.map_or((time, frame.delta, frame.animation_index), |sample| {
                (time, sample.delta, sample.index)
            })
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

pub fn track(id: &Id) {
    FRAME.with_borrow_mut(|frame| frame.elements.insert(id.clone()));
}

pub fn namespace_element<E: Element>(element: E, namespace: usize) -> NamespacedElement<E> {
    let element = NamespacedElement::new(element, namespace);
    // Layer visibility is reported under the ID used by the wrapped element.
    FRAME.with_borrow_mut(|frame| {
        if frame.elements.contains(element.as_ref().id()) {
            frame.elements.insert(element.id().clone());
        }
    });
    element
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

pub fn native_active(output: &Output) -> bool {
    active(output)
        && output_state(output)
            .timing
            .lock()
            .unwrap()
            .cadence
            .interval
            .is_none()
}

fn deadline(output: &Output) -> Option<Instant> {
    if !active(output) {
        return None;
    }
    output_state(output).timing.lock().unwrap().cadence.deadline
}

pub fn schedule_tick<'a, T: 'a>(
    handle: &LoopHandle<'a, T>,
    output: &Output,
    pending: &mut bool,
    mut redraw: impl FnMut(&mut T, &Output, bool) + 'a,
) {
    if !*pending && let Some(next) = deadline(output) {
        let output = output.clone();
        handle
            .insert_source(Timer::from_deadline(next), move |_, _, state| {
                let next = deadline(&output);
                if let Some(next) = next
                    && next > Instant::now()
                {
                    return TimeoutAction::ToInstant(next);
                }
                redraw(state, &output, next.is_some());
                TimeoutAction::Drop
            })
            .expect("Failed to schedule Forge animation");
        *pending = true;
    }
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

    #[test]
    fn changing_rate_preserves_elapsed_phase() {
        let start = Instant::now();
        let mut clock = Clock::new(start, 1.0);
        let first_change = start + Duration::from_secs(600);
        clock.set_rate(first_change, 3.0);
        assert_eq!(clock.seconds(first_change), 600.0);

        let second_change = first_change + Duration::from_secs(2);
        clock.set_rate(second_change, 0.25);
        assert_eq!(clock.seconds(second_change), 606.0);
        assert_eq!(clock.seconds(second_change + Duration::from_secs(4)), 607.0);
    }

    #[test]
    fn focused_clock_freezes_phase_and_resumes_at_the_new_rate() {
        let start = Instant::now();
        let first = start + Duration::from_millis(50);
        let paused = start + Duration::from_secs(3);
        let resumed = paused + Duration::from_millis(50);
        let next = resumed + Duration::from_millis(50);
        let mut clock = Clock::new(start, 1.0);
        let mut state = None;
        FRAME.with_borrow_mut(|frame| frame.sampled = Some(start));
        FocusClock::sample(&mut state, "rain", true, Some(clock));
        let state = state.as_mut().unwrap();
        state.advance(first, Some(start), true, Some(clock));
        let phase = state.phase;
        state.advance(paused, Some(first), false, Some(clock));
        clock.set_rate(paused, 2.0);
        state.advance(resumed, Some(paused), true, Some(clock));
        assert_eq!(state.phase.time, phase.time);
        assert_eq!(state.phase.index, phase.index);
        assert_eq!(state.phase.delta, phase.delta);
        assert_eq!(state.phase.clock, phase.clock);

        state.advance(next, Some(resumed), true, Some(clock));
        assert_eq!(state.phase.previous, Some(phase.time));
        assert_eq!(state.phase.time, first + Duration::from_millis(50));
        assert_eq!(state.phase.index, phase.index + 1);
        assert!((state.phase.clock.unwrap() - 0.15).abs() < 1e-6);

        state.advance(next + Duration::from_secs(20), None, true, Some(clock));
        assert_eq!(state.phase.time, first + Duration::from_millis(50));
    }

    #[test]
    fn focused_clock_advances_with_interleaved_output_ticks() {
        let start = Instant::now();
        let mut state = None;
        FRAME.with_borrow_mut(|frame| frame.sampled = Some(start));
        FocusClock::sample(&mut state, "rain", true, None);
        let state = state.as_mut().unwrap();
        let mut previous = [Some(start), Some(start)];
        for tick in 1..=20 {
            let output = tick % 2;
            let sampled = start + Duration::from_millis(tick as u64 * 16);
            state.advance(sampled, previous[output], true, None);
            previous[output] = Some(sampled);
        }
        assert_eq!(state.phase.time - start, Duration::from_millis(320));
        let phase = state.phase;
        state.advance(start + Duration::from_millis(310), previous[0], true, None);
        assert_eq!(state.phase.time, phase.time);
        state.advance(
            start + Duration::from_secs(3),
            Some(start + Duration::from_secs(2)),
            true,
            None,
        );
        assert_eq!(state.phase.time, phase.time);
    }

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
        uniforms(&id, false, [10.0; 2], None);
        assert_eq!(output_size(), [1920.0, 1080.0]);
        assert!(!active(&first));
        begin_frame(&second);
        uniforms(&id, false, [20.0; 2], None);
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
        output_state(&first).timing.lock().unwrap().cadence.sampled =
            Some(Instant::now() - Duration::from_secs(5));
        begin_frame(&first);
        FRAME.with_borrow(|frame| assert_eq!((frame.delta, frame.index), (0.25, 1)));
        let static_values = uniforms(&Id::new(), false, [80.0, 90.0], None);
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
        uniforms(&id, true, [0.0; 2], None);
        track(&id);
        finish_frame(&first, &states);
        assert!(active(&first));
        assert!(!active(&second));
        begin_frame(&first);
        uniforms(&id, false, [0.0; 2], None);
        finish_frame(&first, &states);
        assert!(!active(&first));
        begin_frame(&first);
        uniforms(&id, true, [0.0; 2], None);
        track(&id);
        states.states.get_mut(&id).unwrap().visible_area = 0;
        finish_frame(&first, &states);
        assert!(!active(&first));
        begin_frame(&first);
        uniforms(&id, true, [0.0; 2], None);
        track(&id);
        finish_frame(&first, &RenderElementStates::default());
        assert!(!active(&first));
    }

    #[test]
    fn a_visible_animated_layer_keeps_its_output_active() {
        use crate::backend::render::element::DamageElement;

        let output = output("layer");
        let layer = DamageElement::new(Default::default());
        let id = layer.id().clone();
        begin_frame(&output);
        uniforms(&id, true, [0.0; 2], None);
        track(&id);
        let rendered_id = namespace_element(layer, 7).id().clone();
        assert_ne!(rendered_id, id);
        let mut states = RenderElementStates::default();
        states.states.insert(
            rendered_id,
            RenderElementState {
                visible_area: 8,
                presentation_state: RenderElementPresentationState::Rendering { reason: None },
                needs_capture: false,
            },
        );
        finish_frame(&output, &states);
        assert!(active(&output));
        begin_frame(&output);
        finish_frame(&output, &RenderElementStates::default());
        assert!(!active(&output));
    }

    #[test]
    fn capped_animation_reuses_the_last_sample_on_faster_scene_frames() {
        let start = Instant::now();
        let interval = interval(30, Some(120_000));
        let mut cadence = Cadence::default();
        assert!(cadence.sample(start, interval, false));
        for millis in [8, 16, 25, 33] {
            assert!(!cadence.sample(start + Duration::from_millis(millis), interval, true));
            assert_eq!(cadence.sampled, Some(start));
        }
        let next = start + Duration::from_millis(34);
        assert!(cadence.sample(next, interval, true));
        assert_eq!(cadence.sampled, Some(next));
        assert_eq!(cadence.index, 1);
        assert!(cadence.deadline.unwrap() - start < Duration::from_millis(67));
        assert!(cadence.sample(start + Duration::from_secs(20), interval, false));
    }

    #[test]
    fn frame_rate_caps_only_outputs_faster_than_the_limit() {
        assert!(interval(0, Some(144_000)).is_none());
        assert!(interval(60, Some(60_000)).is_none());
        assert!(interval(120, Some(60_000)).is_none());
        assert_eq!(
            interval(120, Some(144_000)),
            Some(Duration::from_secs(1) / 120)
        );
    }

    #[test]
    fn motion_uses_logical_desktop_coordinates_and_resets_after_a_pause() {
        let first = output("first");
        first.change_current_state(None, None, None, Some((1280, -100).into()));
        let id = Id::new();
        begin_frame(&first);
        let values = uniforms(&id, true, [20.0, 40.0], None);
        assert!(matches!(values[3].value, UniformValue::_2f(1300.0, -60.0)));
        assert!(matches!(values[4].value, UniformValue::_2f(0.0, 0.0)));
        let second = output("second");
        begin_frame(&second);
        let values = uniforms(&id, true, [200.0, 300.0], None);
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
