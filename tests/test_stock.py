"""Stock xteink_app 7.2.4 (ESP-IDF 6.0.1) on the emulator.

Needs the raw 16 MB device dump (images/device/flash-2026-09-06-a.bin, gitignored: it holds the
owner's WiFi credentials) and the device card's files (images/device/sd-files: the stock's external
system font, cache boot plans and state; the `stock_card` fixture builds a card image from them once
per session). Skips otherwise.
The fixture boots a scratch copy of the dump; `net_en` selects the NVS user_config/net_en value
(tools/nvsedit.py): 0 keeps the stock off the radio, 1 (the device's setting) starts WiFi at 14 s.

What the stock does here (docs/log.md 2026-09-06): boot-preflight rejects a plain power-on (it wants
the power button held for its "effective" 600 ms when it samples GPIO3 at ~530 ms) and deep sleeps.
`x4emu run --boot-hold-power` holds the button from reset instead, so the preflight accepts the cold
boot (`hold=600ms decision=0 reason=11`) and the deep-sleep/wake detour is gone; a power press after
the deep sleep is the other way in (`test_stock_boots_to_home_lights_and_reacts_to_touch` no longer
takes it either). It then mounts the card, loads NVS, initialises the UC8279 through
ESP-IDF's interrupt+GDMA spi_master driver, paints its home screen ("Bookshelf") and lights the warm
frontlight channel. With WiFi enabled the PHY calibrates against the analog-master I2C block, the SENS
temperature sensor and the radio register stub, the driver prints "wifi:mode : sta" as on the device,
then finds no air: "TX Q not empty" at +7.5 s, "force witi stop", and the stock deinitialises WiFi at
+17 s while the UI keeps running (no radio is modelled; docs/NEXT_PHASE.md). The golden is
emulator-made (no device oracle for this screen yet; the device shows the same empty bookshelf). The
status bar (clock, battery, WiFi glyph) is excluded from the comparison.

Sixty seconds after the last input the stock fades the frontlight out (ESP-IDF ledc fade, 255 steps
of one duty unit every 24 PWM periods); the first touch fades it back in. That fade is what froze
the emulator before the LEDC model walked the duty in guest time (docs/log.md 2026-09-06, session 4):
ESP-IDF's fade-end ISR reads DUTY_R back, sees a duty that never moved, re-arms the fade, and the
instant "fade done" interrupt storms core 0's level-1 dispatcher forever, taking the FreeRTOS tick
with it. `test_stock_idle_dims_frontlight_and_keeps_ticking` guards that.
"""
import json, os, shutil, subprocess, sys, time, pytest
from conftest import x4emu, ROOT, PY

DUMP = os.path.join(ROOT, 'images', 'device', 'flash-2026-09-06-a.bin')
GOLDEN = os.path.join(ROOT, 'tests', 'golden', 'stock-home.png')
STATUS_BAR_COLS = 60     # landscape columns 0..59 hold the portrait status bar (clock, battery)
PANEL_W = 800


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


SD_FILES = os.path.join(ROOT, 'images', 'device', 'sd-files')


@pytest.fixture(scope='session')
def stock_card(tmp_path_factory):
    """The device's card as `tools/mksd.py` builds it from images/device/sd-files (its XTData system
    font, XTCache boot plans, .crosspoint state) plus tests/mkepub.py's book at the root: 256 MiB,
    built once per session; every test boots its own copy (the stock writes to the card)."""
    if not os.path.isdir(SD_FILES):
        pytest.skip('images/device/sd-files not available')
    d = tmp_path_factory.mktemp('card')
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mkepub import make_epub
    make_epub(str(d / 'Test Book.epub'))
    img = d / 'sd-device-full.img'
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'mksd.py'), str(img), '--size', '256M', '--src', SD_FILES], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['mcopy', '-i', f'{img}@@1048576', '-o', str(d / 'Test Book.epub'), '::/'], check=True)
    return str(img)


@pytest.fixture
def stock(tmp_path, request, stock_card):
    """A scratch copy of the dump (user_config/net_en = request.param, default 0) and of the full
    device card with the test book on it, booted with the power button held from reset
    (`--boot-hold-power`) so the boot-preflight accepts the cold boot; see `boot_to_home`."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    net_en = getattr(request, 'param', 0)
    name = 'pytest-' + request.node.name.replace('[', '-').replace(']', '').replace('=', '-')   # '=' breaks -qmp unix:PATH
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'set-u8', 'user_config', 'net_en', str(net_en)],
                   check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd), '--trace-epd', str(tmp_path / 'epd.jsonl'),
          '--boot-hold-power')
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def masked_diff(a_png, b_png, cols=(STATUS_BAR_COLS, PANEL_W)):
    """Pixels differing in landscape columns cols[0]..cols[1]-1 (the portrait status bar with the
    clock and battery is columns 0..59; the nav menu's date sits in the last 60 columns)."""
    from PIL import Image, ImageChops
    a = Image.open(a_png).convert('1'); b = Image.open(b_png).convert('1')
    box = (cols[0], 0, min(cols[1], a.width), a.height)
    d = ImageChops.difference(a.crop(box), b.crop(box))
    return sum(1 for v in d.get_flattened_data() if v) if hasattr(d, 'get_flattened_data') else sum(1 for v in d.getdata() if v)


def golden_check(shot, name, cols=(STATUS_BAR_COLS, PANEL_W)):
    """Compare against tests/golden/NAME (created from this shot when missing)."""
    g = os.path.join(ROOT, 'tests', 'golden', name)
    if not os.path.exists(g):
        shutil.copyfile(shot, g)
        print(f'golden created: {g}')
    n = masked_diff(shot, g, cols)
    assert n == 0, f'{os.path.basename(shot)} differs from tests/golden/{name} in {n} pixels (columns {cols[0]}..{cols[1]-1})'


def test_stock_boots_to_home_lights_and_reacts_to_touch(stock):
    name, tmp = stock
    # 1. boot-preflight rejects the cold boot and deep-sleeps; the power button wakes it
    wait_for(lambda: 'deep sleep' in x4emu(name, 'status').stdout, 60, 'preflight deep sleep')
    x4emu(name, 'press', 'power')
    x4emu(name, 'wait-text', 'main_task: Returned from app_main()', '--timeout', '60')
    # 2. panel init + home paint through spi_master + GDMA: refresh 1 (init, three 60,000-byte planes)
    #    and 2 (Home); the third is the clock at the next minute (see boot_to_home)
    st = wait_for(lambda: (s := state(name))['refresh_count'] >= 2 and s, 90, 'home paint')
    assert st['spi2']['dma_tx_bytes'] > 280000, st['spi2']      # >= 4 planes of 60,000 / 48,000 bytes over DMA
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


def boot_to_home(name):
    """Preflight deep sleep, power press, Home painted (refresh 2: the panel init's full refresh is 1,
    Home's plane is 2) and the panel idle. Refresh 3 is the status-bar clock, which the stock completes
    at the next wall-clock minute: it pre-sends the old plane right after every refresh and sends the
    new plane + DRF when the minute changes (or at once for a UI event), so waiting for a third
    refresh takes 0..60 s. Returns the state after the paint."""
    wait_for(lambda: 'deep sleep' in x4emu(name, 'status').stdout, 60, 'preflight deep sleep')
    x4emu(name, 'press', 'power')
    x4emu(name, 'wait-text', 'main_task: Returned from app_main()', '--timeout', '60')
    st = wait_for(lambda: (s := state(name))['refresh_count'] >= 2 and s, 90, 'home paint')
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    return st


def hot_polls(st):
    """(address, reads+writes) of every unmodelled or stubbed register polled past the log cap."""
    rows = [(h['addr'], h['reads'] + h['writes']) for h in st.get('rf', {}).get('hot', [])]
    for block, hs in st.get('iolog_hot', {}).items():
        rows += [(f"{block} {h['addr']}", h['reads'] + h['writes']) for h in hs]
    return rows


@pytest.mark.parametrize('stock', [1], indirect=True, ids=['wifi_on'])
def test_stock_wifi_fails_fast(stock):
    """With the device's NVS (WiFi on) the radio start must not park core 0: the PHY's polls on the
    analog-master I2C block (0x6000E050), the temperature sensor (SENS 0x50) and the radio blocks
    (FE 0x174, MAC 0xD14) are answered, the driver reaches "wifi:mode : sta" and gives up the way
    ESP-IDF does without a link. Regression guard for docs/log.md 2026-09-06 (WiFi start)."""
    name, tmp = stock
    boot_to_home(name)
    # 1. the PHY and the driver come up exactly as on the device (docs/device/boot-stock-7.2.4.log)
    x4emu(name, 'wait-text', 'phy_init: phy_version 711', '--timeout', '60')
    x4emu(name, 'wait-text', 'wifi:mode : sta (98:c3:77:be:ea:30)', '--timeout', '60')
    x4emu(name, 'wait-text', 'wifi:enable tsf', '--timeout', '30')
    st = state(name)
    assert st['ana_i2c']['reads'] > 500 and st['ana_i2c']['sar2_starts'] >= 1, st['ana_i2c']   # PHY calibration ran
    assert st['saradc']['tsens_reads'] >= 1, st['saradc']
    # 2. no air: the driver stops the radio instead of spinning (host-bounded: ~8 s of guest time)
    x4emu(name, 'wait-text', 'wifi:force witi stop', '--timeout', '90')
    # 3. meanwhile core 0 kept running: the gauge poll (every 3 s) and the panel are alive
    i2c0 = state(name)['i2c0']['transactions']
    t = state(name)['uptime_us']
    wait_for(lambda: state(name)['uptime_us'] > t + 7_000_000, 40, 'seven guest seconds')
    st = state(name)
    assert st['i2c0']['transactions'] > i2c0, 'CW2017 polling stopped: core 0 is starved'
    # 4. nothing spins on an unmodelled register: a real spin makes millions of reads per second
    assert all(n < 200_000 for _, n in hot_polls(st)), hot_polls(st)
    # 5. the home screen is intact (status bar masked: clock, battery, WiFi glyph)
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    shot = tmp / 'home-net.png'
    x4emu(name, 'screenshot', str(shot))
    assert masked_diff(shot, GOLDEN) == 0, 'home screen differs from tests/golden/stock-home.png outside the status bar'
    # 6. and it still takes input: the menu icon repaints
    before = state(name)
    x4emu(name, 'tap', '88', '38', '--quiet', '1')
    after = wait_for(lambda: (s := state(name))['refresh_count'] > before['refresh_count'] and s, 20, 'repaint after the menu tap')
    assert after['gt911']['frames'] > before['gt911']['frames']


def test_stock_idle_dims_frontlight_and_keeps_ticking(stock):
    """After ~60 s without input the stock fades the warm channel to 0 (LEDC fade, guest time); the
    tick, the gauge poll and touch must survive it, and the first tap fades the light back in."""
    name, tmp = stock
    st = boot_to_home(name)
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')

    def warm(s):
        return {c['gpio']: c for c in s['ledc']['channels'] if c['gpio'] >= 0}.get(9, {})
    assert warm(st)['duty_permille'] > 0, warm(st)
    # 1. the auto-dim: a real fade (steps in guest time), ending at 0
    seen_fade = False
    def dimmed():
        nonlocal seen_fade
        s = state(name)
        w = warm(s)
        seen_fade |= bool(w.get('fading'))
        return s if w.get('duty_permille') == 0 and s['ledc']['fades'] >= 1 else None
    st = wait_for(dimmed, 120, 'frontlight auto-dim (~60 s after the wake)')
    assert st['ledc']['fades'] >= 1, st['ledc']
    # 2. core 0 still ticks: the gauge poll (every 3 s) goes on, the panel clock still repaints
    i2c0, t = st['i2c0']['transactions'], st['uptime_us']
    wait_for(lambda: state(name)['uptime_us'] > t + 7_000_000, 40, 'seven guest seconds')
    assert state(name)['i2c0']['transactions'] > i2c0, 'CW2017 polling stopped: core 0 is stuck (LEDC fade-end storm?)'
    # 3. touch works and brings the light back (a fade in, to the previous duty)
    before = state(name)
    x4emu(name, 'tap', '88', '38', '--quiet', '1')
    after = wait_for(lambda: (s := state(name))['refresh_count'] > before['refresh_count'] and s, 20, 'repaint after the menu tap')
    assert after['gt911']['clears'] > before['gt911']['clears']
    def lit_again():
        s = state(name)
        return s if warm(s)['duty_permille'] > 0 and not warm(s)['fading'] else None
    lit = wait_for(lit_again, 10, 'frontlight back on')
    assert lit['ledc']['fades'] >= 2, lit['ledc']


def tap(name, x, y, quiet=1, wait=20):
    x4emu(name, 'tap', str(x), str(y), '--quiet', str(quiet), '--wait', str(wait))


def settle(name, seconds=1.5, timeout=60):
    x4emu(name, 'wait-quiet', '--seconds', str(seconds), '--timeout', str(timeout))


def test_stock_screens_walk(stock):
    """Home -> nav menu -> All Files -> the EPUB (page 1, page 2) -> reading menu -> back to the
    bookshelf -> nav menu -> Settings, one golden per screen (tests/golden/stock-*.png, emulator-made;
    no device oracle yet). Coordinates are landscape panel pixels of the portrait UI: the hamburger
    icon (88, 38), the nav menu entries at x 160/245/330/415/500 (Read, All Files, USB Mode, Cloud
    Sync, Settings), the first file row (250, 320), the page centre (400, 240), the reading menu's
    back arrow (80, 445). The Home pad does nothing in the stock (docs/NEXT_PHASE.md)."""
    name, tmp = stock
    boot_to_home(name)
    settle(name, 2, 30)
    tap(name, 88, 38); settle(name)
    x4emu(name, 'screenshot', str(tmp / 'menu.png'))
    golden_check(tmp / 'menu.png', 'stock-menu.png', cols=(STATUS_BAR_COLS, PANEL_W - 60))   # date on the right edge
    tap(name, 245, 150); settle(name, 2)
    x4emu(name, 'screenshot', str(tmp / 'all-files.png'))
    golden_check(tmp / 'all-files.png', 'stock-all-files.png')
    tap(name, 250, 320, wait=30); settle(name, 3, 120)          # opening the book: several refreshes
    x4emu(name, 'screenshot', str(tmp / 'reader-page1.png'))
    READER_COLS = (STATUS_BAR_COLS, PANEL_W - 40)     # the reader draws its clock in the portrait footer: the last 40 columns
    golden_check(tmp / 'reader-page1.png', 'stock-reader-page1.png', cols=READER_COLS)
    x4emu(name, 'press', 'right', '--quiet', '1', '--wait', '20'); settle(name, 2)
    x4emu(name, 'screenshot', str(tmp / 'reader-page2.png'))
    golden_check(tmp / 'reader-page2.png', 'stock-reader-page2.png', cols=READER_COLS)
    assert masked_diff(tmp / 'reader-page1.png', tmp / 'reader-page2.png') > 1000, 'page turn drew nothing'
    tap(name, 400, 240); settle(name, 2)
    x4emu(name, 'screenshot', str(tmp / 'reading-menu.png'))
    golden_check(tmp / 'reading-menu.png', 'stock-reading-menu.png')
    tap(name, 80, 445); settle(name, 2)                          # back: the bookshelf now lists the book
    x4emu(name, 'screenshot', str(tmp / 'bookshelf.png'))
    assert masked_diff(tmp / 'bookshelf.png', GOLDEN) > 1000, 'bookshelf still empty after reading'
    tap(name, 88, 38); settle(name)
    tap(name, 500, 150); settle(name, 2)
    x4emu(name, 'screenshot', str(tmp / 'settings.png'))
    golden_check(tmp / 'settings.png', 'stock-settings.png')
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']


# --- the frontlight controls (the stock's pull-down "BrightnessLayer") ---------------------------
# Landscape panel pixels of the portrait control panel; portrait (px, py) is landscape (py, 479-px).
LIGHT_PULL = (5, 240, 300, 240)      # portrait (240, 5) -> (240, 300): a slow pull from the top edge
BRI_MINUS, BRI_PLUS = (130, 433), (130, 45)       # portrait (46, 130) / (434, 130)
CT_MINUS, CT_PLUS = (245, 433), (245, 45)         # portrait (46, 245) / (434, 245)
LIGHT_BTN = (307, 60)                # the "Light" sun icon, portrait (419, 307)
BACKDROP = (600, 240)                # the dimmed page behind the panel: a tap there closes it


def light_duty(name, timeout=20):
    """(cool GPIO8, warm GPIO9) LEDC duty in permille, read once neither channel is fading."""
    def settled():
        ch = {c['gpio']: c for c in state(name)['ledc']['channels'] if c['gpio'] in (8, 9)}
        if ch[8]['fading'] or ch[9]['fading']:
            return None
        return (ch[8]['duty_permille'], ch[9]['duty_permille'])
    return wait_for(settled, timeout, 'the LEDC fade to finish')


def light_step(name, x, y, want, tries=3, timeout=8):
    """Tap a -/+ button of a slider until the frontlight reads `want`. A tap the firmware dropped is
    repeated; one it took is never repeated (the value is already there), so no step is doubled."""
    for _ in range(tries):
        tap(name, x, y)
        t0 = time.time()
        while time.time() - t0 < timeout:
            got = light_duty(name)
            if got == want:
                return got
            time.sleep(0.3)
    raise AssertionError(f'tap ({x},{y}) never brought the frontlight to {want}; last {light_duty(name)}')


def nvs_u8(flash, key, ns='user_config'):
    """The u8 `tools/nvsedit.py IMAGE list` reports for NS/KEY, or None (the last copy wins)."""
    out = subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'list'],
                         capture_output=True, text=True, check=True).stdout
    hits = [ln.rsplit('= ', 1)[1].strip() for ln in out.splitlines() if f'{ns}/{key} (u8) = ' in ln]
    return int(hits[-1]) if hits else None


def test_stock_light_controls_follow_the_sliders(stock):
    """The stock's frontlight UI is a pull-down panel (the app's "BrightnessLayer"), not a Settings
    page: a slow drag from the portrait top edge downwards opens it over any page.

    Path: Home -> swipe landscape (5, 240) -> (300, 240) with --ms 600 (portrait (240, 5) -> (240,
    300), i.e. straight down from the status bar). The panel covers the top ~370 portrait rows
    (landscape columns 0..369) and dims the page behind it. It holds, top to bottom in portrait:
      "Brightness %d%%" + a slider    -- "-" at landscape (130, 433), "+" at (130, 45), track
                                         between landscape y 389 (min) and 124 (max)
      "Color Temp <preset>" + slider  -- "-" at landscape (245, 433), "+" at (245, 45); the presets
                                         are Cool 4..Cool 1, Balanced, Warm 1..Warm 4
      four buttons: Boost (307, 419), Full Refresh (307, 299), Sleep Lock (307, 179),
                    Light (307, 60)  -- "Light" toggles the frontlight off/on (crossed-out sun)
    A tap on the dimmed page, landscape (600, 240), closes the panel; swiping back up does not.

    The dump's NVS has user_config/lightBri = 100 and lightCT = 100, so the panel opens at
    "Brightness 100%" / "Color Temp Warm 4" with the warm channel (GPIO9) alone at 249 permille.
    Each "-" on brightness is 10 % of the duty; each "-" on colour temperature moves one preset,
    mixing the cool channel (GPIO8) in and the warm one out. Both are written back to NVS at once
    (lightBri, lightCT, lightOn), which `tools/nvsedit.py` reads out of the live flash image."""
    name, tmp = stock
    boot_to_home(name)
    settle(name, 2, 30)

    # 1. pull the panel down and check it against the golden (status bar masked: clock, battery)
    x4emu(name, 'swipe', *map(str, LIGHT_PULL), '--ms', '600')
    settle(name, 1.5)
    shot = tmp / 'light.png'
    x4emu(name, 'screenshot', str(shot))
    golden_check(shot, 'stock-light.png')

    # 2. as opened: 100 % / Warm 4 = the warm channel alone (the pull re-lit it if it had auto-dimmed)
    def lit():
        v = light_duty(name)
        return v if v != (0, 0) else None
    assert wait_for(lit, 20, 'the frontlight to be on') == (0, 249)

    # 3. brightness "-" twice: 100 % -> 90 % -> 80 % of the duty, colour mix unchanged
    ladder = [light_step(name, *BRI_MINUS, want=(0, 224)), light_step(name, *BRI_MINUS, want=(0, 199))]
    # 4. colour temperature "-" four times: Warm 4 -> Balanced, cool up and warm down at every step
    for want in ((28, 175), (59, 149), (87, 125), (119, 99)):
        ladder.append(light_step(name, *CT_MINUS, want=want))
    assert [c for c, _ in ladder] == sorted(c for c, _ in ladder), ladder      # cool rises
    assert [w for _, w in ladder] == sorted((w for _, w in ladder), reverse=True), ladder
    settle(name, 1.5)
    shot2 = tmp / 'light-adjusted.png'
    x4emu(name, 'screenshot', str(shot2))
    golden_check(shot2, 'stock-light-adjusted.png')      # "Brightness 80%", "Color Temp Balanced"
    assert masked_diff(shot, shot2) > 500, 'the panel did not redraw its labels and handles'

    # 5. the stock stores both in NVS user_config straight away
    flash = tmp / 'stock.bin'
    wait_for(lambda: nvs_u8(flash, 'lightBri') == 80, 20, 'lightBri = 80 in NVS')
    assert nvs_u8(flash, 'lightCT') == 50, nvs_u8(flash, 'lightCT')            # 100 = Warm 4, 50 = Balanced

    # 6. the "Light" button switches the frontlight off and on again (and persists lightOn)
    off = light_step(name, *LIGHT_BTN, want=(0, 0))
    assert off == (0, 0)
    wait_for(lambda: nvs_u8(flash, 'lightOn') == 0, 20, 'lightOn = 0 in NVS')
    assert light_step(name, *LIGHT_BTN, want=(119, 99)) == (119, 99), 'the light did not come back'
    wait_for(lambda: nvs_u8(flash, 'lightOn') == 1, 20, 'lightOn = 1 in NVS')

    # 7. a tap on the dimmed page closes the panel and Home is back, pixel for pixel
    tap(name, *BACKDROP)
    settle(name, 2)
    back = tmp / 'light-closed.png'
    x4emu(name, 'screenshot', str(back))
    assert masked_diff(back, GOLDEN) == 0, 'Home did not come back after closing the light panel'
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']
