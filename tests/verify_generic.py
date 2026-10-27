#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the neutral glass pipeline in an isolated nested compositor."""
import json
import os
from pathlib import Path
import time
from PIL import ImageChops, ImageGrab, ImageStat

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(os.environ['CRYSTAL_QA_DIR'])
STATE = json.loads((RUN / 'state.json').read_text())
CONFIG = RUN / 'config/cosmic/com.system76.CosmicComp/v1'
SOURCE = (ROOT / 'glass.frag').read_text()
KEYS = ['forge_runtime', 'glass_effect', 'aero_blur_amount', 'crystal_blur_amount',
        'crystal_thickness', 'crystal_everyday']
saved = {key: (CONFIG / key).read_bytes() if (CONFIG / key).exists() else None for key in KEYS}
results = []


def write(key, text):
    temporary = CONFIG / ('.' + key + '.new')
    temporary.write_text(text + '\n')
    temporary.replace(CONFIG / key)
    time.sleep(0.8)


def runtime(source=SOURCE, blur=45, identifier='plain-glass', shift=0, base='glass'):
    parameters = '(key:"forge_plain_blur",name:"forge_blur",value:' + str(float(blur)) + ')'
    if 'uniform float forge_shift;' in source:
        parameters += ',(key:"forge_plain_shift",name:"forge_shift",value:' + str(float(shift)) + ')'
    write('forge_runtime', '(abi:1,id:' + json.dumps(identifier) + ',base:' + json.dumps(base) + ',source:' +
          json.dumps(source) + ',parameters:[' + parameters + '])')
    write('glass_effect', json.dumps(identifier))


def grab():
    return ImageGrab.grab(xdisplay=STATE['display']).convert('RGB')


def difference(first, second):
    return sum(ImageStat.Stat(ImageChops.difference(first, second)).mean) / 3


def check(name, condition):
    results.append({'test': name, 'passed': bool(condition)})
    print(('PASS ' if condition else 'FAIL ') + name, flush=True)
    (RUN / 'generic-render-results.json').write_text(json.dumps(results, indent=2) + '\n')
    assert condition, name


try:
    time.sleep(1)
    runtime(blur=0)
    clear = grab()
    runtime(blur=100)
    blurred = grab()
    blurred.save(RUN / 'generic-blurred.png')
    check('Generic blur changes real compositor output', difference(clear, blurred) > 1)
    for key, value in [('aero_blur_amount', '0'), ('crystal_blur_amount', '100'),
                       ('crystal_thickness', '100'), ('crystal_everyday', 'true')]:
        write(key, value)
    check('Generic glass ignores Aero and Crystal controls', difference(blurred, grab()) < 0.4)
    runtime(SOURCE.replace('texture2D(tex, v_coords)', 'texture2D(sharp_tex, v_coords)'), blur=100)
    check('Sharp backdrop stays available alongside the blurred backdrop', difference(clear, grab()) < 0.5)
    runtime(blur=100, identifier='another-glass')
    check('A second generic effect can select its own blur', difference(clear, grab()) > 1)
    runtime(blur=0)
    check('Returning to the first generic effect restores its blur', difference(clear, grab()) < 0.4)
    diagnostic = SOURCE.replace('    color *= mask * alpha;',
                                '    color.rgb *= clamp(output_scale * 0.4, 0.0, 1.0);\n    color *= mask * alpha;')
    runtime(diagnostic, blur=0)
    actual_scale = grab()
    runtime(diagnostic.replace('output_scale * 0.4', '1.0 * 0.4'), blur=0)
    check('Output scale is supplied to generic shaders', difference(actual_scale, grab()) < 0.4)
    palette = SOURCE.replace('    color *= mask * alpha;',
                             '    color.rgb = mix(theme_tint, theme_accent, 0.5) * color.a;\n    color *= mask * alpha;')
    runtime(palette, blur=0)
    generic_palette = grab()
    runtime(palette, blur=0, base='aero')
    check('Generic glass receives the current theme palette and accent', difference(generic_palette, grab()) < 0.4)
    pixel_probe = SOURCE.replace('    color *= mask * alpha;',
        '    color.rgb *= step(0.0000001, uv_per_pixel.x) * step(0.0000001, uv_per_pixel.y);\n    color *= mask * alpha;')
    runtime(pixel_probe, blur=0)
    check('Texture pixel steps are available to generic shaders', difference(clear, grab()) < 0.4)
    custom = SOURCE.replace('void main() {', 'uniform float forge_shift;\nvoid main() {').replace(
        '    color *= mask * alpha;',
        '    color.rgb = mix(color.rgb, color.brg, clamp(forge_shift / 100.0, 0.0, 1.0));\n    color *= mask * alpha;')
    runtime(custom, blur=0, shift=100)
    check('Custom uniforms work on the generic base', difference(clear, grab()) > 1)
    runtime('Invalid GLSL', blur=0)
    check('Invalid generic GLSL falls back to neutral glass', difference(clear, grab()) < 0.4)
    runtime(blur=0)
    check('Corrected generic GLSL recovers live', difference(clear, grab()) < 0.4)
    check('The real compositor remains running', Path(STATE['env']['XDG_RUNTIME_DIR'], 'cosmic-forge-engine-v1').is_file())
finally:
    for key, data in saved.items():
        if data is None:
            (CONFIG / key).unlink(missing_ok=True)
        else:
            (CONFIG / key).write_bytes(data)
