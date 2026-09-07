"""The stock's own Developer -> Screen Capture, end to end: patch, boot, capture, decode, diff.

One test, and it is the oracle behind `tools/xic2png.py`: it boots the stock `xteink_app` 7.2.4 with
developer mode enabled (`tools/stockdev.py` on a scratch copy of the dump: in 7.2.4 the predicate is
a compile-time stub that returns 0, docs/xic.md), takes an `x4emu screenshot` of Home, pulls the
light panel down, taps **Screen Capture**, copies the `/screenshots/screenshot_*.xic` the firmware
wrote off the card with mtools, and checks that the decoded `.xic` is the same image as the
screenshot. Nothing else in the tree compares the emulator's panel with a *firmware-side* rendering
of the same frame: the panel PNG comes out of the UC8279 model's framebuffer, the `.xic` comes out of
the app's own compositor through the SD write path, so 0 differing pixels says the two agree about
every pixel of Home, and that the decoder reads the container the way the writer wrote it.

The status bar is masked (landscape columns 0..59: clock and battery), because the clock minute can
roll over between the screenshot and the capture -- that is exactly the 69-pixel difference the first
manual run showed (docs/xic.md). Everything else must match exactly.

The capture holds the last **fully painted** frame, not the live compositor, so the translucent light
panel that is on screen when the button is tapped does not appear in it: the file is Home, which is
why the screenshot is taken before the swipe.

Needs the device dump and the device card's files, like the rest of tests/test_stock.py; skips
otherwise (CI never has them). The patched flash copy lives in pytest's tmp_path only -- it still
carries the owner's WiFi credentials, CLAUDE.md rule 6.
"""
import os, shutil, subprocess, sys
import pytest
from conftest import x4emu, ROOT, PY

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import xic2png                                                                 # noqa: E402
from test_stock import (DUMP, STATUS_BAR_COLS, boot_to_home, masked_diff,      # noqa: E402
                        settle, state, stock_card)                             # noqa: F401

FAT_OFFSET = 1048576             # tools/mksd.py puts the FAT32 partition at 1 MiB
LIGHT_PULL = ('5', '240', '300', '240')     # portrait (240,5) -> (240,300): pull the panel down
SCREEN_CAPTURE = ('735', '199')             # the "Screen Capture" button, portrait ~ (280, 735)
CAPTURE_BYTES = 48024                       # 24-byte header + 480x800 at 1 bpp


@pytest.fixture
def stock_dev(tmp_path, request, stock_card):
    """`test_stock.stock` with developer mode on: the same scratch copy of the dump and of the
    device card, `net_en` 0, `--boot-hold-power`, but `tools/stockdev.py` run over the flash image
    (app0 at 0x10000) before the boot, so the light panel offers Memory / Developer / Screen
    Capture. Yields (instance name, tmp_path, card image)."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    name = 'pytest-' + request.node.name.replace('[', '-').replace(']', '').replace('=', '-')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    for cmd in (['nvsedit.py', str(flash), 'set-u8', 'user_config', 'net_en', '0'],
                ['stockdev.py', str(flash)]):
        subprocess.run([PY, os.path.join(ROOT, 'tools', cmd[0]), *cmd[1:]],
                       check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd),
          '--trace-epd', str(tmp_path / 'epd.jsonl'), '--boot-hold-power')
    yield name, tmp_path, sd
    x4emu(name, 'stop', check=False)


def captures_on(card):
    """The `/screenshots/screenshot_*.xic` on the card, newest last (the name is a timestamp)."""
    out = subprocess.run(['mdir', '-i', f'{card}@@{FAT_OFFSET}', '-b', '::/screenshots'],
                         capture_output=True, text=True)
    assert out.returncode == 0, f'mdir failed: {out.stdout}\n{out.stderr}'
    return sorted(ln.strip() for ln in out.stdout.splitlines()
                  if ln.strip().endswith('.xic') and '/screenshot_' in ln)


def test_stock_screen_capture_writes_the_home_screen_as_xic(stock_dev):
    """Home -> pull the light panel down -> Screen Capture -> the `.xic` on the card is Home."""
    name, tmp, card = stock_dev
    boot_to_home(name)
    settle(name, 2, 30)
    home = tmp / 'home.png'
    x4emu(name, 'screenshot', str(home))      # the base frame the capture will hold

    # 1. the developer buttons only exist because of the patch: pull the panel and tap Screen Capture
    x4emu(name, 'swipe', *LIGHT_PULL, '--ms', '600')
    settle(name, 1.5)
    for attempt in range(2):                  # the stock drops a tap that lands right after another
        before = state(name)['refresh_count']
        x4emu(name, 'tap', *SCREEN_CAPTURE, '--ms', '120')
        settle(name, 3)
        if state(name)['refresh_count'] > before:
            break                             # the "Screenshot saved" toast painted
    else:
        raise AssertionError('the Screen Capture tap never produced a repaint (no toast)')
    x4emu(name, 'stop')                       # flush the card image before mtools reads it

    # 2. the file the firmware wrote, off the card
    shots = captures_on(card)
    assert shots, 'the stock wrote no /screenshots/screenshot_*.xic'
    subprocess.run(['mcopy', '-i', f'{card}@@{FAT_OFFSET}', '-o', shots[-1], str(tmp)], check=True)
    got = tmp / os.path.basename(shots[-1])
    data = got.read_bytes()
    assert len(data) == CAPTURE_BYTES, f'{got.name} is {len(data)} bytes, expected {CAPTURE_BYTES}'

    # 3. the header says what docs/xic.md says it says
    im, h = xic2png.decode_xic(data)
    assert (h['width'], h['height']) == (480, 800), h
    assert (h['version'], h['levels'], h['planes']) == (1, 1, 1), h
    assert h['payload_bytes'] == 48000 == h['stride'] * h['height'] * h['planes'], h
    assert h['file_bytes'] == CAPTURE_BYTES

    # 4. and the pixels are the panel's, once rotated back into the landscape frame; the status bar
    #    is masked because the clock minute can roll over between the screenshot and the capture
    from PIL import Image
    decoded = tmp / 'capture.png'
    im.transpose(Image.Transpose.ROTATE_90).save(decoded)
    assert Image.open(decoded).size == Image.open(home).size == (800, 480)
    n = masked_diff(decoded, home, cols=(STATUS_BAR_COLS, 800))
    assert n == 0, (f'{got.name} differs from the panel screenshot in {n} pixels outside the status '
                    f'bar (columns {STATUS_BAR_COLS}..799); both are in {tmp}')
