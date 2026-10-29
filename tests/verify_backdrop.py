#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Check backdrop sources, focus and theme on real isolated compositor output."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from PIL import ImageChops, ImageGrab, ImageStat
from Xlib import X, XK, display
from Xlib.error import ConnectionClosedError
from Xlib.ext import xtest

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(os.environ['CRYSTAL_QA_DIR'])
STATE = json.loads((RUN / 'state.json').read_text())
CONFIG = RUN / 'config/cosmic/com.system76.CosmicComp/v1'
MODE = RUN / 'config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark'
SOURCE = (ROOT / 'glass.frag').read_text()
keys = ['forge_runtime', 'forge_backdrop', 'glass_effect', 'aero_blur_amount',
        'crystal_blur_amount', 'crystal_thickness', 'crystal_everyday']
saved = {CONFIG / key: (CONFIG / key).read_bytes() if (CONFIG / key).exists() else None for key in keys}
saved[MODE] = MODE.read_bytes() if MODE.exists() else None
processes = []
results = []
pointer = display.Display(STATE['display'])
move_key = pointer.keysym_to_keycode(XK.string_to_keysym('Super_L'))


def check(name, condition):
    results.append({'test': name, 'passed': bool(condition)})
    (RUN / 'backdrop-render-results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(('PASS ' if condition else 'FAIL ') + name, flush=True)
    assert condition, name


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name('.' + path.name + '.new')
    pending.write_text(value + '\n')
    pending.replace(path)


def runtime(source=SOURCE, base='glass', backdrop='scene', blur=0):
    write(CONFIG / 'aero_blur_amount', str(blur))
    write(CONFIG / 'crystal_blur_amount', str(blur))
    write(CONFIG / 'crystal_thickness', '0')
    write(CONFIG / 'crystal_everyday', 'false')
    field = ',backdrop:wallpaper' if backdrop == 'wallpaper' else ''
    write(CONFIG / 'forge_runtime', '(abi:1,id:"backdrop-test",base:' + json.dumps(base) + field +
          ',source:' + json.dumps(source) + ',parameters:[(key:"forge_test_blur",name:"forge_blur",value:' + str(float(blur)) + ')])')
    write(CONFIG / 'glass_effect', '"backdrop-test"')
    time.sleep(.6)


def grab(name=None):
    image = ImageGrab.grab(xdisplay=STATE['display']).convert('RGB')
    if name:
        image.save(RUN / (name + '.png'))
    return image


def origin(image, color=(11, 14, 20)):
    masks = [image.getchannel(i).point([255 if value == component else 0 for value in range(256)])
             for i, component in enumerate(color)]
    mask = ImageChops.multiply(ImageChops.multiply(masks[0], masks[1]), masks[2])
    data = mask.tobytes()
    rows = [data[y * image.width:(y + 1) * image.width] for y in range(image.height)]
    counts = [row.count(255) for row in rows]
    width = max(counts)
    if width < 24:
        return None
    row = rows[counts.index(width)]
    run = max(row.split(b'\x00'), key=len)
    return row.find(run), next(y for y, count in enumerate(counts) if count >= width * .75)


def difference(first, second, box):
    return sum(ImageStat.Stat(ImageChops.difference(first.crop(box), second.crop(box))).mean) / 3


def click(x, y):
    xtest.fake_input(pointer, X.MotionNotify, x=x, y=y)
    xtest.fake_input(pointer, X.ButtonPress, 1)
    xtest.fake_input(pointer, X.ButtonRelease, 1)
    pointer.sync()
    time.sleep(.3)


def drag(start, target):
    x, y = start[0] + 20, start[1] + 35
    xtest.fake_input(pointer, X.MotionNotify, x=x, y=y)
    xtest.fake_input(pointer, X.KeyPress, move_key)
    pointer.sync()
    time.sleep(.08)
    xtest.fake_input(pointer, X.ButtonPress, 1)
    pointer.sync()
    time.sleep(.08)
    for step in range(1, 13):
        xtest.fake_input(pointer, X.MotionNotify, x=x + int((target[0] - start[0]) * step / 12),
                         y=y + int((target[1] - start[1]) * step / 12))
        pointer.sync()
        time.sleep(.025)
    xtest.fake_input(pointer, X.ButtonRelease, 1)
    xtest.fake_input(pointer, X.KeyRelease, move_key)
    pointer.sync()
    time.sleep(.4)


def spawn(mode):
    process = subprocess.Popen([str(ROOT / 'build/bin/test-surface'), mode], env=STATE['env'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    processes.append(process)
    time.sleep(.8)
    return process


def output_mode(scale, transform):
    subprocess.run(['cosmic-randr', 'mode', 'WINIT-0', '1280', '900', '--scale', scale,
                    '--transform', transform], env=STATE['env'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
    time.sleep(1.5)


try:
    try:
        for scale, transform in [('1.25', 'flipped180'), ('1', 'normal'), ('1.25', 'rotate90')]:
            output_mode(scale, transform)
            runtime()
            scene = grab()
            runtime(backdrop='wallpaper')
            wallpaper = grab('output-' + scale + '-' + transform)
            check('Wallpaper matches the scene at scale ' + scale + ' and transform ' + transform,
                  difference(scene, wallpaper, (0, 0, scene.width, scene.height)) < .4)
    finally:
        output_mode('1', 'flipped180')
    runtime()
    initial = grab()
    drag(origin(initial), (230, 112))
    initial = grab()
    left, top = origin(initial)
    body = (left + 25, top + 220, left + 190, top + 420)
    header = (left + 25, top + 8, left + 790, top + 80)
    for base in ['glass', 'aero', 'crystal']:
        runtime(base=base)
        scene = grab()
        runtime(base=base, backdrop='wallpaper')
        wallpaper = grab('wallpaper-' + base)
        check(base + ' wallpaper sampling matches the unoccluded scene', difference(scene, wallpaper, body) < .4)
        check(base + ' source changes preserve opaque foreground', difference(scene, wallpaper, header) == 0)
        check(base + ' source changes preserve clipping', difference(scene, wallpaper, (left - 5, top - 5, left + 8, top + 8)) < .1)

    runtime(backdrop='wallpaper')
    wallpaper = grab()
    overlay = spawn('--background-overlay')
    desktop = (left + 300, top + 240, left + 550, top + 420)
    check('Non-wallpaper desktop layers are excluded from wallpaper', difference(wallpaper, grab(), desktop) < .4)
    runtime()
    check('Scene retains non-wallpaper desktop layers', difference(wallpaper, grab(), desktop) > 5)
    os.killpg(overlay.pid, signal.SIGTERM)
    overlay.wait(timeout=5)
    time.sleep(.3)

    spawn('--small-opaque')
    auxiliary = origin(grab(), (50, 60, 70))
    assert auxiliary, 'Auxiliary window was not mapped'
    drag(auxiliary, (left - 100, top + 170))
    left, top = origin(grab())
    body = (left + 25, top + 220, left + 190, top + 420)
    click(left + 650, top + 35)
    grab('underlay-placement')
    sharp = SOURCE.replace('texture2D(tex, v_coords)', 'texture2D(sharp_tex, v_coords)')
    for base in ['glass', 'aero', 'crystal']:
        runtime(base=base)
        scene = grab('scene-under-' + base)
        runtime(base=base, backdrop='wallpaper')
        wallpaper = grab('wallpaper-under-' + base)
        check(base + ' scene includes the window beneath the surface', difference(scene, wallpaper, body) > 5)
        check(base + ' wallpaper excludes the underlying window', difference(initial, wallpaper, body) < .4)
        runtime(base=base, backdrop='wallpaper', blur=100)
        blurred = grab()
        check(base + ' wallpaper can be blurred', difference(wallpaper, blurred, body) > 1)
        runtime(sharp, base=base, backdrop='wallpaper', blur=100)
        check(base + ' wallpaper sharp_tex remains original at maximum blur', difference(wallpaper, grab(), body) < .4)
        runtime(sharp, base=base, blur=100)
        check(base + ' scene sharp_tex remains original at maximum blur', difference(scene, grab(), body) < .4)

    runtime(backdrop='wallpaper')
    before = grab()
    auxiliary = origin(before, (50, 60, 70))
    drag(auxiliary, (left - 180, top + 210))
    moved_auxiliary = origin(grab(), (50, 60, 70))
    check('The underlying window really moved', moved_auxiliary is not None and abs(moved_auxiliary[0] - auxiliary[0]) > 30)
    click(left + 650, top + 35)
    check('Moving an underlying window leaves wallpaper output unchanged', difference(before, grab(), body) < .4)
    auxiliary = origin(grab(), (50, 60, 70))
    drag(auxiliary, (0, 640))
    click(left + 650, top + 35)
    drag((left, top), (left + 120, top + 40))
    moved = grab('wallpaper-moved')
    moved_left, moved_top = origin(moved)
    check('The shader surface really moved', moved_left - left > 90)
    runtime()
    sample = (moved_left + 350, moved_top + 220, moved_left + 700, moved_top + 420)
    check('Moving a wallpaper surface changes its sample coordinates correctly', difference(moved, grab(), sample) < .4)
    drag((moved_left, moved_top), (left, top))
    left, top = origin(grab())
    sample = (left + 600, top + 400)
    diagnostic = SOURCE.replace('void main() {', 'uniform float window_active;\nuniform float is_dark;\nvoid main() {').replace(
        '    color *= mask * alpha;', '    color.rgb = vec3(window_active, is_dark, 0.0) * color.a;\n    color *= mask * alpha;')
    for base in ['glass', 'aero', 'crystal']:
        runtime(diagnostic, base=base)
        click(left + 650, top + 35)
        active = grab().getpixel(sample)
        auxiliary = origin(grab(), (50, 60, 70))
        click(auxiliary[0] + 100, auxiliary[1] + 30)
        inactive = grab().getpixel(sample)
        check(base + ' window_active changes live without a client repaint', active[0] > 180 and inactive[0] < 20)
        write(MODE, 'true')
        time.sleep(.7)
        dark = grab().getpixel(sample)
        write(MODE, 'false')
        time.sleep(.7)
        light = grab().getpixel(sample)
        check(base + ' is_dark follows actual system mode', dark[1] > 180 and light[1] < 20)

    runtime(diagnostic, backdrop='wallpaper')
    popup_process = spawn('--popup')
    owner = origin(grab(), (100, 40, 60))
    assert owner, 'Popup owner was not mapped'
    drag(owner, (80, 200))
    owner = origin(grab(), (100, 40, 60))
    click(owner[0] + 150, owner[1] + 35)
    image = grab('focus-popup')
    popup = origin(image, (32, 48, 64))
    assert popup, 'Dependent popup was not mapped'
    point = (popup[0] + 100, popup[1] + 80)
    check('A dependent popup inherits its focused owner', image.getpixel(point)[0] > 180)
    auxiliary = origin(image, (50, 60, 70))
    click(auxiliary[0] + 100, auxiliary[1] + 30)
    image = grab()
    check('An unfocused owner and its popup become inactive together',
          image.getpixel((owner[0] + 150, owner[1] + 400))[0] < 20 and
          (origin(image, (32, 48, 64)) is None or image.getpixel(point)[0] < 20))
    os.killpg(popup_process.pid, signal.SIGTERM)
    popup_process.wait(timeout=5)

    transient_process = spawn('--transient')
    owner = origin(grab(), (100, 40, 60))
    child = origin(grab(), (32, 48, 64))
    assert owner and child, 'Transient owner and child were not mapped'
    click(owner[0] + 150, owner[1] + 35)
    image = grab('focus-transient-owner')
    child = origin(image, (32, 48, 64))
    point = (child[0] + 100, child[1] + 80)
    check('A transient window inherits its active owner', image.getpixel(point)[0] > 180)
    click(child[0] + 100, child[1] + 15)
    image = grab('focus-transient-child')
    owner = origin(image, (100, 40, 60))
    check('A focused transient still follows its owner activation',
          abs(image.getpixel(point)[0] - image.getpixel((owner[0] + 150, owner[1] + 400))[0]) < 5)

    spawn('--panel')
    image = grab('focus-panel')
    panel = origin(image, (80, 90, 100))
    assert panel, 'Ownerless layer surface was not mapped'
    point = (panel[0] + 150, panel[1] + 200)
    check('An ownerless panel receives window_active = 1', image.getpixel(point)[0] > 180)
    click(owner[0] + 150, owner[1] + 35)
    check('Window focus changes leave ownerless panels active', grab().getpixel(point)[0] > 180)
finally:
    try:
        xtest.fake_input(pointer, X.ButtonRelease, 1)
        xtest.fake_input(pointer, X.KeyRelease, move_key)
        pointer.sync()
        pointer.close()
    except (OSError, ConnectionClosedError):
        pass
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
    for path, content in saved.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)
