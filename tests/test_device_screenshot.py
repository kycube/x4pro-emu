"""Oracle: the screenshot the real X4 Pro wrote to its card (CrossPoint 1.6.0, home screen,
100% and charging, docs/device/screenshots/screenshot-3824.bmp) must be reproduced pixel for
pixel by the emulator once the battery state matches."""
import os, subprocess
from conftest import x4emu, ROOT

DEVICE_BMP = os.path.join(ROOT, 'tests', 'golden', 'device-home-screenshot-3824.bmp')

def test_home_screen_matches_device_screenshot(images, emu, tmp_path):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    # as booted: default gauge 63%, not charging -> only the status bar differs
    r = x4emu(emu, 'screenshot', str(tmp_path / 'home-63.png'), '--diff', DEVICE_BMP, check=False)
    pct = float(r.stdout.split('%')[0].split()[-1])
    assert pct < 0.2, r.stdout
    # match the device: 100%, USB power -> repaint via a Browse Files round trip
    x4emu(emu, 'battery', '--soc', 100, '--charging', 'on')
    # the percentage change repaints the status bar within ~1.5 s; do not tap into that paint
    x4emu(emu, 'tap', 345, 350, '--quiet', 3, '--wait', 20)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    x4emu(emu, 'home', '--wait', 20)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    r = x4emu(emu, 'screenshot', str(tmp_path / 'home-100.png'), '--diff', DEVICE_BMP, check=False)
    assert r.returncode == 0 and '0.000%' in r.stdout, r.stdout
