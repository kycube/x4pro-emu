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
    # the Home pad is polled: press it into an idle panel and hold it (default 250 ms)
    x4emu(emu, 'home', '--quiet', 1.5, '--wait', 20)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    r = x4emu(emu, 'screenshot', str(tmp_path / 'home-100.png'), '--diff', DEVICE_BMP, check=False)
    assert r.returncode == 0 and '0.000%' in r.stdout, r.stdout


# --- the reader: two pages of the test book, screenshot by the device (2026-09-07) -------------
# The owner opened tests/mkepub.py's book on the device (CrossPoint 1.6.0) and took the screenshot
# chord on pages 1 and 2 (Power + Down writes /screenshots/<book>/<book>_chN_pM_..bmp). The device
# BMP is the firmware's 1-bit base frame (every anti-aliased glyph pixel black), so the emulator's
# page is compared as its base frame too: any ink -> black. The only pixels that then differ are
# the footer clock digits (the device's time against the emulator's): landscape x 780..792,
# y 404..461 in the first comparison (307 pixels); that box is masked.
DEVICE_PAGE1 = os.path.join(ROOT, 'tests', 'golden', 'device-reader-page1-16905.bmp')
DEVICE_PAGE2 = os.path.join(ROOT, 'tests', 'golden', 'device-reader-page2-47729.bmp')
CLOCK_BOX = (770, 396, 800, 470)     # landscape box around the portrait footer clock


def base_frame_diff(emu_png, device_bmp, mask=CLOCK_BOX):
    """Pixels differing between the emulator's page (as a base frame) and the device's BMP
    (un-rotated to the landscape panel), outside `mask`."""
    from PIL import Image, ImageChops, ImageDraw
    emu = Image.open(emu_png).convert('L').point(lambda v: 255 if v >= 255 else 0)
    dev = Image.open(device_bmp).convert('L')
    if dev.size == (emu.size[1], emu.size[0]):
        dev = dev.rotate(90, expand=True)
    dev = dev.point(lambda v: 255 if v >= 128 else 0)
    d = ImageChops.difference(emu, dev)
    if mask:
        ImageDraw.Draw(d).rectangle(mask, fill=0)
    data = d.get_flattened_data() if hasattr(d, 'get_flattened_data') else d.getdata()
    return sum(1 for v in data if v)


def test_reader_pages_match_device_screenshots(images, emu, tmp_path):
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', images['sd'], '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    x4emu(emu, 'tap', 345, 350, '--quiet', 2, '--wait', 20)            # Browse Files
    x4emu(emu, 'wait-text', 'Entering activity: FileBrowser', '--timeout', 30)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    x4emu(emu, 'tap', 140, 240, '--quiet', 1.5, '--wait', 20)          # the book
    x4emu(emu, 'wait-text', 'Loaded ePub: /Test Book.epub', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 3, '--timeout', 120)
    p1 = tmp_path / 'page1.png'
    x4emu(emu, 'screenshot', str(p1))
    n1 = base_frame_diff(p1, DEVICE_PAGE1)
    assert n1 == 0, f'page 1 differs from the device screenshot in {n1} pixels outside the clock'
    x4emu(emu, 'press', 'right', '--wait', 20)
    x4emu(emu, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    p2 = tmp_path / 'page2.png'
    x4emu(emu, 'screenshot', str(p2))
    n2 = base_frame_diff(p2, DEVICE_PAGE2)
    assert n2 == 0, f'page 2 differs from the device screenshot in {n2} pixels outside the clock'
    # the wrong page must not match: the oracle is discriminating
    assert base_frame_diff(p2, DEVICE_PAGE1) > 10000
