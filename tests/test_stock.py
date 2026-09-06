"""Stock xteink_app 7.2.4 (ESP-IDF 6.0.1) on the emulator.

Needs the raw 16 MB device dump (images/device/flash-2026-09-06-a.bin, gitignored: it holds the
owner's WiFi credentials) and the device's SD contents (images/sd-device.img). Skips otherwise.
The fixture boots a scratch copy of the dump with user_config/net_en=0 (tools/nvsedit.py), because
the stock's WiFi start parks its high-priority radio task in a ROM spin on the analog-master I2C
block (0x6000E050), which no model answers yet (docs/NEXT_PHASE.md), starving everything else.

What the stock does here (docs/log.md 2026-09-06): boot-preflight rejects a plain power-on and deep
sleeps; a power press wakes it; it mounts the card, loads NVS, initialises the UC8279 through
ESP-IDF's interrupt+GDMA spi_master driver, paints its home screen ("Bookshelf") and lights the warm
frontlight channel. The golden is emulator-made (no device oracle for this screen yet; the device
shows the same empty bookshelf). The status bar (clock, battery) is excluded from the comparison.
"""
import json, os, shutil, subprocess, time, pytest
from conftest import x4emu, ROOT, PY

DUMP = os.path.join(ROOT, 'images', 'device', 'flash-2026-09-06-a.bin')
SD = os.path.join(ROOT, 'images', 'sd-device.img')
GOLDEN = os.path.join(ROOT, 'tests', 'golden', 'stock-home.png')
STATUS_BAR_COLS = 60     # landscape columns 0..59 hold the portrait status bar (clock, battery)


def state(name):
    return json.loads(x4emu(name, 'state').stdout)


def wait_for(fn, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.5)
    raise AssertionError(f'timeout waiting for {what}')


@pytest.fixture
def stock(tmp_path, request):
    if not (os.path.exists(DUMP) and os.path.exists(SD)):
        pytest.skip('device dump / SD image not available')
    name = 'pytest-' + request.node.name
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'set-u8', 'user_config', 'net_en', '0'],
                   check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(SD, sd)          # the stock writes to the card at boot
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd), '--trace-epd', str(tmp_path / 'epd.jsonl'))
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def masked_diff(a_png, b_png):
    from PIL import Image, ImageChops
    a = Image.open(a_png).convert('1'); b = Image.open(b_png).convert('1')
    box = (STATUS_BAR_COLS, 0, a.width, a.height)
    d = ImageChops.difference(a.crop(box), b.crop(box))
    return sum(1 for v in d.getdata() if v)


def test_stock_boots_to_home_lights_and_reacts_to_touch(stock):
    name, tmp = stock
    # 1. boot-preflight rejects the cold boot and deep-sleeps; the power button wakes it
    wait_for(lambda: 'deep sleep' in x4emu(name, 'status').stdout, 60, 'preflight deep sleep')
    x4emu(name, 'press', 'power')
    x4emu(name, 'wait-text', 'main_task: Returned from app_main()', '--timeout', '60')
    # 2. panel init + home paint through spi_master + GDMA (three refreshes: full, then partial windows)
    st = wait_for(lambda: (s := state(name))['refresh_count'] >= 3 and s, 90, 'three refreshes')
    assert st['spi2']['dma_tx_bytes'] > 300000, st['spi2']      # >= 3 planes of 60,000 bytes over DMA
    assert st['spi2']['dma_errors'] == 0, st['spi2']
    assert st['epd_unknown_cmds'] == 0, st
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    shot = tmp / 'home.png'
    x4emu(name, 'screenshot', str(shot))
    if not os.path.exists(GOLDEN):
        shutil.copyfile(shot, GOLDEN)
        print(f'golden created: {GOLDEN}')
    assert masked_diff(shot, GOLDEN) == 0, 'home screen differs from tests/golden/stock-home.png outside the status bar'
    # 3. the frontlight is on: the stock drives the warm channel (GPIO9) through LEDC
    duty = {c['gpio']: c['duty_permille'] for c in st['ledc']['channels'] if c['gpio'] >= 0}
    assert duty.get(9, 0) > 0, duty
    # 4. alive past the point where WiFi would have started (14 s): the gauge is still polled
    i2c0 = st['i2c0']['transactions']
    wait_for(lambda: state(name)['uptime_us'] > st['uptime_us'] + 6_000_000, 30, 'six guest seconds')
    assert state(name)['i2c0']['transactions'] > i2c0
    # 5. touch: the menu icon repaints; the stock reads the GT911 on its INT line
    before = state(name)
    x4emu(name, 'tap', '88', '38', '--quiet', '1')
    after = wait_for(lambda: (s := state(name))['refresh_count'] > before['refresh_count'] and s, 20, 'repaint after the menu tap')
    assert after['gt911']['frames'] > before['gt911']['frames']
    assert after['gpio_irqs'] > before['gpio_irqs']
