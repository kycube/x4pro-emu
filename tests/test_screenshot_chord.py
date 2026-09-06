"""CrossPoint's own screenshot (Power + Down chord -> /screenshots/*.bmp on the card) must equal
the emulator's panel screenshot pixel for pixel: the panel model shows what the firmware wrote."""
import os, shutil, subprocess
from conftest import x4emu, ROOT

def test_device_style_bmp_matches_panel(images, emu, tmp_path):
    sd = tmp_path / 'sd-chord.img'
    shutil.copyfile(images['sd'], sd)          # the firmware writes to the card
    x4emu(emu, 'run', '--flash', images['flash'], '--sd', str(sd), '--fast-epd')
    x4emu(emu, 'wait-text', 'Entering activity: Home', '--timeout', 90)
    x4emu(emu, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    png = tmp_path / 'panel.png'
    x4emu(emu, 'screenshot', str(png))
    x4emu(emu, 'chord', 'power', 'right', '--ms', 300)
    x4emu(emu, 'wait-text', 'Screenshot saved to /screenshots/', '--timeout', 30)
    x4emu(emu, 'stop')
    part = f'{sd}@@1048576'
    names = subprocess.run(['mdir', '-i', part, '-b', '::/screenshots'], capture_output=True, text=True, check=True).stdout.split()
    bmps = [n for n in names if n.lower().endswith('.bmp')]
    assert bmps, names
    bmp = tmp_path / 'device-style.bmp'
    subprocess.run(['mcopy', '-i', part, '-n', bmps[0], str(bmp)], check=True)
    from PIL import Image, ImageChops
    a = Image.open(png).convert('L')
    b = Image.open(bmp).convert('L')
    assert b.size == (480, 800)
    b = b.rotate(90, expand=True)
    diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 64 else 0)
    n = sum(1 for v in list(diff.getdata()) if v)
    assert n == 0, f'{n} pixels differ between the panel and the firmware BMP'
