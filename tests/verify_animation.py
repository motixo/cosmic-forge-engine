#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Verify opt-in animation on real nested compositor output in private XDG."""
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from PIL import ImageChops, ImageGrab, ImageStat
from Xlib import X, XK, display
from Xlib.ext import xtest

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(os.environ['CRYSTAL_QA_DIR'])
STATE = json.loads((RUN / 'state.json').read_text())
CONFIG = RUN / 'config/cosmic/com.system76.CosmicComp/v1'
SOURCE = (ROOT / 'glass.frag').read_text()
saved = {key: (CONFIG / key).read_bytes() if (CONFIG / key).exists() else None
         for key in ['forge_runtime', 'glass_effect']}
results = []
cover = None
pointer = None
move_key = None
mover = None


def check(name, condition):
    results.append({'test': name, 'passed': bool(condition)})
    (RUN / 'animation-render-results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(('PASS ' if condition else 'FAIL ') + name, flush=True)
    assert condition, name


def write(key, value):
    pending = CONFIG / ('.' + key + '.new')
    pending.write_text(value + '\n')
    pending.replace(CONFIG / key)


def runtime(source, animated=True, speed=100, base='glass'):
    parameters = '(key:"forge_animation_blur",name:"forge_blur",value:0.0),' + \
                 '(key:"forge_animation_speed",name:"forge_speed",value:' + str(float(speed)) + ')'
    write('forge_runtime', '(abi:1,id:"animation-test",base:' + json.dumps(base) + ',source:' +
          json.dumps(source) + ',animated:' + str(animated).lower() + ',parameters:[' + parameters + '])')
    write('glass_effect', '"animation-test"')
    time.sleep(.7)


def shader(body):
    return SOURCE.replace('void main() {', 'uniform float time;\nuniform float delta_time;\n'
                          'uniform int frame_index;\nuniform vec2 window_position;\n'
                          'uniform vec2 window_velocity;\nuniform float forge_speed;\nvoid main() {').replace(
                          '    color *= mask * alpha;', body + '\n    color *= mask * alpha;')


def grab(name=None):
    image = ImageGrab.grab(xdisplay=STATE['display']).convert('RGB')
    if name:
        image.save(RUN / (name + '.png'))
    return image


def difference(first, second, box):
    return sum(ImageStat.Stat(ImageChops.difference(first.crop(box), second.crop(box))).mean) / 3


def origin(image):
    points = [(x, y) for y in range(image.height) for x in range(image.width)
              if image.getpixel((x, y)) == (11, 14, 20)]
    assert points, 'Opaque Wayland calibration header was not found'
    return min(x for x, _ in points), min(y for _, y in points)


def body_sample(image):
    x, y = origin(image)
    return image.getpixel((x + 720, y + 400))


try:
    runtime(SOURCE, animated=False)
    neutral = grab()
    left, top = origin(neutral)
    body = (left + 180, top + 220, left + 700, top + 420)
    header = (left + 20, top + 8, left + 800, top + 80)
    pulse = shader('    float phase = time * forge_speed * 0.03;\n'
                   '    vec3 wave = 0.5 + 0.5 * vec3(sin(phase), cos(phase), sin(phase * 0.71));\n'
                   '    color.rgb = mix(color.rgb, wave * color.a, 0.8);')
    for base in ['glass', 'aero', 'crystal']:
        runtime(pulse, base=base)
        before = grab('animation-' + base)
        unchanged = {p: p.stat().st_mtime_ns for p in CONFIG.iterdir() if p.is_file()}
        time.sleep(.25)
        after = grab()
        check(base + ' animates while its Wayland client and settings are idle', difference(before, after, body) > 2)
        check(base + ' keeps opaque foreground pixels unchanged', difference(before, after, header) == 0)
        check(base + ' advances without rewriting config', unchanged == {p: p.stat().st_mtime_ns for p in unchanged})
    runtime(pulse, animated=False)
    before = grab()
    time.sleep(.25)
    check('The same shader stays static when animation is disabled', difference(before, grab(), body) < .05)
    runtime(pulse, speed=0)
    before = grab()
    time.sleep(.25)
    check('Custom speed controls remain live without recompiling', difference(before, grab(), body) < .05)
    runtime(pulse)
    before = grab()
    time.sleep(.25)
    check('Changing speed restores motion', difference(before, grab(), body) > 2)
    for uniform, expression in [('delta_time', 'step(0.000001, delta_time)'),
                                 ('frame_index', '(frame_index > 0 ? 1.0 : 0.0)')]:
        diagnostic = shader('    color.rgb = vec3(' + expression + ', 0.0, 0.0) * color.a;')
        runtime(diagnostic, animated=False)
        zero = grab()
        runtime(diagnostic)
        check(uniform + ' receives real per-output frame data', difference(zero, grab(), body) > 20)
    runtime(pulse)
    cover = subprocess.Popen([str(ROOT / 'build/bin/test-surface'), '--cover'], env=STATE['env'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(1)
    before = grab('animation-covered')
    time.sleep(.25)
    check('A fully opaque fullscreen surface hides all animated pixels', difference(before, grab(), body) < .05)
    os.killpg(cover.pid, signal.SIGTERM)
    cover.wait(timeout=5)
    cover = None
    time.sleep(.7)
    before = grab()
    time.sleep(.25)
    check('Uncovering a surface resumes animation', difference(before, grab(), body) > 2)
    runtime(SOURCE, animated=False)
    neutral = grab()
    rejected_before = (RUN / 'compositor.log').read_text().count('Forge shader rejected')
    runtime('Invalid GLSL')
    check('Invalid animated GLSL falls back to static neutral rendering', difference(neutral, grab(), body) < .4)
    time.sleep(.3)
    check('Invalid animated GLSL is not recompiled every frame', (RUN / 'compositor.log').read_text().count('Forge shader rejected') == rejected_before + 1)
    runtime(pulse)
    before = grab()
    time.sleep(.25)
    check('Corrected GLSL recovers animation without restarting', difference(before, grab(), body) > 2)
    motion = shader('    color.rgb = vec3(clamp(window_position / vec2(1280.0, 900.0), 0.0, 1.0),\n'
                    '                     step(1.0, length(window_velocity))) * color.a;')
    runtime(motion)
    before = grab('motion-before')
    start = origin(before)
    resting = body_sample(before)
    check('Stationary windows receive zero velocity', resting[2] < 10)
    pointer = display.Display(STATE['display'])
    move_key = pointer.keysym_to_keycode(XK.string_to_keysym('Super_L'))
    xtest.fake_input(pointer, X.MotionNotify, x=start[0] + 350, y=start[1] + 40)
    xtest.fake_input(pointer, X.KeyPress, move_key)
    pointer.sync()
    time.sleep(.1)
    xtest.fake_input(pointer, X.ButtonPress, 1)
    pointer.sync()
    time.sleep(.1)

    def drag():
        for step in range(1, 41):
            xtest.fake_input(pointer, X.MotionNotify, x=start[0] + 350 + step * 3,
                             y=start[1] + 40 + int(step * 1.2))
            pointer.sync()
            time.sleep(.025)

    mover = threading.Thread(target=drag)
    mover.start()
    moving = []
    while mover.is_alive():
        moving.append(grab().getpixel((start[0] + 600, start[1] + 400)))
        time.sleep(.01)
    mover.join()
    xtest.fake_input(pointer, X.ButtonRelease, 1)
    xtest.fake_input(pointer, X.KeyRelease, move_key)
    pointer.sync()
    time.sleep(.4)
    after = grab('motion-after')
    end = origin(after)
    stationary = body_sample(after)
    check('The native Wayland window actually moved', end[0] - start[0] > 80 and end[1] - start[1] > 25)
    check('Position inputs follow the moving surface', stationary[0] > resting[0] + 10 and stationary[1] > resting[1] + 5)
    check('Velocity inputs respond while dragging', any(sample[2] > 100 for sample in moving))
    check('Velocity returns to zero after dragging stops', abs(stationary[2] - resting[2]) < 2)
    xtest.fake_input(pointer, X.MotionNotify, x=end[0] + 350, y=end[1] + 40)
    xtest.fake_input(pointer, X.KeyPress, move_key)
    pointer.sync()
    time.sleep(.1)
    xtest.fake_input(pointer, X.ButtonPress, 1)
    pointer.sync()
    time.sleep(.1)
    xtest.fake_input(pointer, X.MotionNotify, x=start[0] + 350, y=start[1] + 40)
    xtest.fake_input(pointer, X.ButtonRelease, 1)
    xtest.fake_input(pointer, X.KeyRelease, move_key)
    pointer.sync()
finally:
    if mover is not None:
        mover.join(timeout=3)
    if pointer is not None:
        xtest.fake_input(pointer, X.ButtonRelease, 1)
        if move_key is not None:
            xtest.fake_input(pointer, X.KeyRelease, move_key)
        pointer.sync()
        pointer.close()
    if cover is not None and cover.poll() is None:
        os.killpg(cover.pid, signal.SIGTERM)
        cover.wait(timeout=5)
    for key, data in saved.items():
        if data is None:
            (CONFIG / key).unlink(missing_ok=True)
        else:
            (CONFIG / key).write_bytes(data)
