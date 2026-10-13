"""Stock xteink_app 7.2.4 (ESP-IDF 6.0.1) on the emulator.

Needs the raw 16 MB device dump (images/device/flash-2026-09-06-a.bin, gitignored: it holds the
owner's WiFi credentials) and the device card's files (images/device/sd-files: the stock's external
system font, cache boot plans and state; the `stock_card` fixture builds a card image from them once
per session). Skips otherwise. The fixture boots a redacted scratch copy of the dump
(tests/stockkit.py); `net_en` selects the NVS user_config/net_en value: 0 keeps the stock off the
radio, 1 (the device's setting) starts WiFi at 14 s.

What the stock does here (docs/log.md 2026-09-06): boot-preflight rejects a plain power-on (it wants
the power button held for its "effective" 600 ms when it samples GPIO3 at ~530 ms) and deep sleeps.
`x4emu run --boot-hold-power` holds the button from reset instead, so the preflight accepts the cold
boot (`hold=600ms decision=0 reason=11`); without the flag a power press after the deep sleep is the
other way in (CLAUDE.md). It then mounts the card, loads NVS, initialises the UC8279 through ESP-IDF's
interrupt+GDMA spi_master driver, paints its home screen ("Bookshelf") and lights the warm frontlight
channel. With WiFi enabled a POWERON boot runs ESP-IDF's *full* PHY calibration ("phy_init: Saving
new calibration data"): the RF PLL cap search and lock poll on analog block 0x62 (reg 0x07 bit 1 is
the lock flag the emulator answers; a stored-value answer printed "phy: error: pll_cal exceeds 2ms!!!"
three times) and the DC-offset search through the analog-master comparator at 0x6000E04C (bit 1
start, bit 24 done, bits 31:30 the comparator outputs; unanswered it parked the WiFi task there for
good, docs/log.md 2026-09-06 S1), the SENS temperature sensor and the radio register stub; the driver
prints "wifi:mode : sta" as on the device, then finds no air: "TX Q not empty" at +7.5 s, "force witi
stop", and the stock deinitialises WiFi at +17 s while the UI keeps running (no radio is modelled;
docs/NEXT_PHASE.md). The goldens are emulator-made (the owner judged them 1:1 with the device by eye);
the status bar (clock, battery, WiFi glyph) is excluded from every comparison.

Sixty seconds after the last input the stock fades the frontlight out (ESP-IDF ledc fade, 255 steps
of one duty unit every 24 PWM periods); the first touch fades it back in. That fade is what froze
the emulator before the LEDC model walked the duty in guest time (docs/log.md 2026-09-06, session 4):
ESP-IDF's fade-end ISR reads DUTY_R back, sees a duty that never moved, re-arms the fade, and the
instant "fade done" interrupt storms core 0's level-1 dispatcher forever, taking the FreeRTOS tick
with it. `test_stock_idle_dims_frontlight_and_keeps_ticking` guards that.
"""
import datetime, json, os, subprocess
import pytest
from conftest import x4emu, ROOT, PY, emu_log
# re-exported too: test_gamebook, test_input_delivery, test_nvsedit, test_stockstrings and test_xtfont
# import DUMP, PANEL_W, STATUS_BAR_COLS, stock_card, stock and the walk helpers from here
from stockkit import (DUMP, GOLDEN, PANEL_W, STATUS_BAR_COLS, MENU_COLS, READER_COLS, HAMBURGER,  # noqa: F401
                      LIGHT_PULL, SKIP_IDLE, booted, boot_to_home, golden_check, ink_in, masked_diff, settle, state,
                      state_if, step, stock_card, tap, tap_repaints, wait_for)


@pytest.fixture
def stock(tmp_path, request, stock_card):
    """The stock booted from a redacted scratch copy of the dump and a copy of the full device card
    with the test book on it (`stockkit.booted`); yields (instance name, tmp_path). A dict
    `request.param` takes `net_en` (default 0), `trace_i2c` (True: `--trace-i2c tmp_path/i2c.jsonl`),
    `icount` (`x4emu run --icount VALUE`, e.g. `SKIP_IDLE`) and `extra` (raw QEMU arguments appended
    after `--`, e.g. a `-global` property)."""
    with booted(request, tmp_path, stock_card, **getattr(request, 'param', {})) as s:
        yield s.name, s.tmp


def guest_ms(st):
    return st['uptime_us'] // 1000


TOAST_COLS = (72, 128)      # landscape columns of the stock's toast box (portrait: the band under the status bar)


def test_stock_boots_to_home_lights_and_reacts_to_touch(stock):
    """The plain configuration (no `--icount`: two MTTCG cores, guest time on the host clock), the
    way CLAUDE.md boots the stock; the other tests here run with `SKIP_IDLE` (tests/stockkit.py)."""
    name, tmp = stock
    # 1. panel init + home paint through spi_master + GDMA: refresh 1 (init, three 60,000-byte planes)
    #    and 2 (Home); the third is the clock at the next minute (see boot_to_home)
    st = boot_to_home(name)
    # 2. the fixture held the power button from reset (`--boot-hold-power`), so boot-preflight
    #    accepted the cold boot (hold=600ms decision=0) instead of deep-sleeping
    log = x4emu(name, 'log').stdout
    assert 'decision=0' in log and 'enter deep sleep' not in log, 'boot-preflight rejected the cold boot'
    assert st['spi2']['dma_tx_bytes'] > 280000, st['spi2']      # >= 4 planes of 60,000 / 48,000 bytes over DMA
    assert st['spi2']['dma_errors'] == 0, st['spi2']
    assert st['epd_unknown_cmds'] == 0, st
    shot = tmp / 'home.png'
    x4emu(name, 'screenshot', shot)
    if masked_diff(shot, GOLDEN) and ink_in(shot, (TOAST_COLS,))[0] > ink_in(GOLDEN, (TOAST_COLS,))[0] + 1000:
        # A toast over Home and the page redrawn: stock 7.2.4's own font-slot race (docs/heap.md). This
        # real-time two-core boot can hit it on a loaded host; it is the firmware's bug, so it is reported,
        # not hidden, and any other difference still fails below.
        pytest.xfail("stock 7.2.4's font-slot race (docs/heap.md): 'External font failed to load', Home "
                     'redrawn in the built-in font')
    golden_check(shot, 'stock-home.png')
    # 3. the frontlight is on: the stock drives the warm channel (GPIO9) through LEDC
    duty = {c['gpio']: c['duty_permille'] for c in st['ledc']['channels'] if c['gpio'] >= 0}
    assert duty.get(9, 0) > 0, duty
    # 3b. the RTC IO pads the stock programs on the way (TOUCH_PAD3/5/6/7/13/14, XTAL_32P/N, PAD_DAC2:
    #     hold / pull / sleep isolation of the buttons, EPD and SD pins) land in the stored-value
    #     overlay, not in the access logger (docs/NEXT_PHASE.md 3.2.9)
    assert st['rtcio']['pad_writes'] > 0 and st['rtcio']['reads'] > 0, st['rtcio']
    assert 'rtcio' not in st['iolog_hot'], st['iolog_hot']
    assert 'x4pro/rtcio' not in emu_log(name, 'qemu.log'), 'RTC IO accesses reached the x4pro/rtcio logger'
    # 4. alive six guest seconds after the Home paint: the gauge is still polled
    x4emu(name, 'wait-guest-until', guest_ms(st) + 6000, '--timeout', 30)
    assert state(name)['i2c0']['transactions'] > st['i2c0']['transactions']
    # 5. touch: the menu icon repaints; the stock reads the GT911 on its INT line
    before, after = tap_repaints(name, *HAMBURGER)
    assert after['gt911']['frames'] > before['gt911']['frames']
    assert after['gpio_irqs'] > before['gpio_irqs']


def hot_polls(st):
    """(address, reads+writes) of every unmodelled or stubbed register polled past the log cap."""
    rows = [(h['addr'], h['reads'] + h['writes']) for h in st.get('rf', {}).get('hot', [])]
    for block, hs in st.get('iolog_hot', {}).items():
        rows += [(f"{block} {h['addr']}", h['reads'] + h['writes']) for h in hs]
    return rows


@pytest.mark.parametrize('stock', [{'net_en': 1, 'icount': SKIP_IDLE}], indirect=True, ids=['wifi_on'])
def test_stock_wifi_fails_fast(stock):
    """With WiFi on (`net_en` 1, the device's setting, and a placeholder SSID in the redacted NVS) the
    radio start must not park core 0: the PHY's
    polls on the analog-master I2C block (0x6000E050, the DC-offset comparator 0x6000E04C, the RF PLL
    lock flag 0x62/0x07 bit 1), the temperature sensor (SENS 0x50) and the radio blocks (FE 0x174, MAC
    0xD14) are answered, the driver reaches "wifi:mode : sta" and gives up the way ESP-IDF does
    without a link. Regression guard for docs/log.md 2026-09-06 (WiFi start) and S1 (the cold boot).

    Booted cold like every other stock test: a POWERON boot makes ESP-IDF run the *full* PHY
    calibration ("Saving new calibration data"), which is the path that used to block after three
    "pll_cal exceeds 2ms" lines (the radio_wifi task spinning on 0x6000E04C bit 24 while the rest of
    the system kept running). The device prints no "pll_cal" line on either path; neither may the
    emulator now."""
    name, tmp = stock
    boot_to_home(name)
    # 1. the PHY and the driver come up exactly as on the device (docs/device/boot-stock-7.2.4.log)
    x4emu(name, 'wait-text', 'phy_init: phy_version 711', '--timeout', '60')
    x4emu(name, 'wait-text', 'wifi:mode : sta (98:c3:77:be:ea:30)', '--timeout', '60')
    x4emu(name, 'wait-text', 'wifi:enable tsf', '--timeout', '30')
    log = x4emu(name, 'log').stdout
    assert 'Saving new calibration data' in log, 'the cold boot did not run the full PHY calibration'
    assert 'pll_cal' not in log, [ln for ln in log.splitlines() if 'pll_cal' in ln]
    st = state(name)
    assert st['ana_i2c']['reads'] > 500 and st['ana_i2c']['sar2_starts'] >= 1, st['ana_i2c']   # PHY calibration ran
    assert st['ana_i2c']['pll_cals'] >= 1 and st['ana_i2c']['cal_starts'] >= 60, st['ana_i2c']  # PLL cal + DC-offset search
    assert st['saradc']['tsens_reads'] >= 1, st['saradc']
    # 2. no air: the driver stops the radio instead of spinning (host-bounded: ~8 s of guest time)
    x4emu(name, 'wait-text', 'wifi:force witi stop', '--timeout', '90')
    # 3. meanwhile core 0 kept running: the gauge poll (every 3 s) and the panel are alive
    i2c0 = state(name)['i2c0']['transactions']
    x4emu(name, 'wait-guest-ms', 7000, '--timeout', 40)
    st = state(name)
    assert st['i2c0']['transactions'] > i2c0, 'CW2017 polling stopped: core 0 is starved'
    # 4. nothing spins on an unmodelled register: a real spin makes millions of reads per second
    assert all(n < 200_000 for _, n in hot_polls(st)), hot_polls(st)
    # 5. the home screen is intact (status bar masked: clock, battery, WiFi glyph)
    settle(name, 2, 30)
    shot = tmp / 'home-net.png'
    x4emu(name, 'screenshot', shot)
    golden_check(shot, 'stock-home.png')
    # 6. and it still takes input: the menu icon repaints
    before, after = tap_repaints(name, *HAMBURGER)
    assert after['gt911']['frames'] > before['gt911']['frames']


def warm(st):
    """The warm frontlight channel (GPIO9) of a state."""
    return {c['gpio']: c for c in st['ledc']['channels'] if c['gpio'] >= 0}.get(9, {})


@pytest.mark.parametrize('stock', [{'icount': SKIP_IDLE}], indirect=True, ids=['skip_idle'])
def test_stock_idle_dims_frontlight_and_keeps_ticking(stock):
    """After ~60 s without input the stock fades the warm channel to 0 (LEDC fade, guest time); the
    tick, the gauge poll and touch must survive it, and the first tap fades the light back in."""
    name, tmp = stock
    st = boot_to_home(name)
    assert warm(st)['duty_permille'] > 0, warm(st)
    # 1. the auto-dim: a hardware fade, ending at 0
    st = wait_for(lambda: state_if(name, lambda s: warm(s).get('duty_permille') == 0 and s['ledc']['fades'] >= 1),
                  120, 'frontlight auto-dim (~60 s after the boot)')
    # 2. core 0 still ticks: the gauge poll (every 3 s) goes on
    x4emu(name, 'wait-guest-until', guest_ms(st) + 7000, '--timeout', 40)
    assert state(name)['i2c0']['transactions'] > st['i2c0']['transactions'], \
        'CW2017 polling stopped: core 0 is stuck (LEDC fade-end storm?)'
    # 3. touch works and brings the light back (a fade in, to the previous duty)
    before, after = tap_repaints(name, *HAMBURGER)
    assert after['gt911']['clears'] > before['gt911']['clears']
    lit = wait_for(lambda: state_if(name, lambda s: warm(s)['duty_permille'] > 0 and not warm(s)['fading']),
                   10, 'frontlight back on')
    assert lit['ledc']['fades'] >= 2, lit['ledc']


@pytest.mark.parametrize('stock', [{'icount': SKIP_IDLE}], indirect=True, ids=['skip_idle'])
def test_stock_screens_walk(stock):
    """Home -> nav menu -> All Files -> the EPUB (page 1, page 2) -> reading menu -> back to the
    bookshelf -> nav menu -> Settings, one golden per screen (tests/golden/stock-*.png). Coordinates
    are landscape panel pixels of the portrait UI: the hamburger icon (88, 38), the nav menu entries
    at x 160/245/330/415/500 (Read, All Files, USB Mode, Cloud Sync, Settings), the first file row
    (250, 320), the page centre (400, 240), the reading menu's back arrow (80, 445). The Home pad is
    Back in the stock (`test_stock_home_pad_acts_as_back`). Every tap that has a golden goes through
    `step()`, which checks the screen it led to."""
    name, tmp = stock
    boot_to_home(name)
    step(name, *HAMBURGER, 'stock-menu.png', cols=MENU_COLS)
    step(name, 245, 150, 'stock-all-files.png', quiet=2)
    page1 = step(name, 250, 320, 'stock-reader-page1.png', cols=READER_COLS, wait=30, quiet=3)  # several refreshes
    x4emu(name, 'press', 'right', '--quiet', '1', '--wait', '20')
    settle(name, 2)
    page2 = tmp / 'reader-page2.png'
    x4emu(name, 'screenshot', page2)
    golden_check(page2, 'stock-reader-page2.png', cols=READER_COLS)
    assert masked_diff(page1, page2) > 1000, 'page turn drew nothing'
    step(name, 400, 240, 'stock-reading-menu.png', quiet=2)
    tap(name, 80, 445)                                            # back: the bookshelf now lists the book
    settle(name, 2)
    shelf = tmp / 'bookshelf.png'
    x4emu(name, 'screenshot', shelf)
    assert masked_diff(shelf, GOLDEN) > 1000, 'bookshelf still empty after reading'
    step(name, *HAMBURGER)                                        # the menu over the filled bookshelf: no golden
    step(name, 500, 150, 'stock-settings.png', quiet=2)
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']


# --- the frontlight controls (the stock's pull-down "BrightnessLayer") ---------------------------
# Landscape panel pixels of the portrait control panel; portrait (px, py) is landscape (py, 479-px).
BRI_MINUS, BRI_PLUS = (130, 433), (130, 45)       # portrait (46, 130) / (434, 130)
CT_MINUS, CT_PLUS = (245, 433), (245, 45)         # portrait (46, 245) / (434, 245)
LIGHT_BTN = (307, 60)                # the "Light" sun icon, portrait (419, 307)
BACKDROP = (600, 240)                # the dimmed page behind the panel: a tap there closes it


def light_duty(name):
    """(cool GPIO8, warm GPIO9) LEDC duty in permille, or None while either channel is fading."""
    ch = {c['gpio']: c for c in state(name)['ledc']['channels'] if c['gpio'] in (8, 9)}
    if ch[8]['fading'] or ch[9]['fading']:
        return None
    return (ch[8]['duty_permille'], ch[9]['duty_permille'])


def light_step(name, x, y, want, timeout=8):
    """Tap a -/+ button of a slider once and wait until the frontlight reads exactly `want`, (cool,
    warm) permille (the step is a hardware fade, so the value lands over guest time)."""
    tap(name, x, y)
    seen = []

    def landed():
        seen.append(light_duty(name))
        return seen[-1] == want
    try:
        wait_for(landed, timeout, '', every=0.25)
    except AssertionError:
        raise AssertionError(f'tap ({x},{y}) did not bring the frontlight to {want}; last {seen[-1]}') from None


def nvs_u8(flash, key, ns='user_config'):
    """The u8 `tools/nvsedit.py IMAGE list` reports for NS/KEY, or None (the last copy wins)."""
    out = subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'list'],
                         capture_output=True, text=True, check=True).stdout
    hits = [ln.rsplit('= ', 1)[1].strip() for ln in out.splitlines() if f'{ns}/{key} (u8) = ' in ln]
    return int(hits[-1]) if hits else None


@pytest.mark.parametrize('stock', [{'icount': SKIP_IDLE}], indirect=True, ids=['skip_idle'])
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

    # 1. pull the panel down and check it against the golden (status bar masked: clock, battery)
    x4emu(name, 'swipe', *LIGHT_PULL, '--ms', '600')
    settle(name, 1.5)
    shot = tmp / 'light.png'
    x4emu(name, 'screenshot', shot)
    golden_check(shot, 'stock-light.png')

    # 2. as opened: 100 % / Warm 4 = the warm channel alone (the pull re-lit it if it had auto-dimmed)
    assert wait_for(lambda: (v := light_duty(name)) != (0, 0) and v, 20, 'the frontlight to be on') == (0, 249)

    # 3. brightness "-" twice: 100 % -> 90 % -> 80 % of the duty, colour mix unchanged
    for want in ((0, 224), (0, 199)):
        light_step(name, *BRI_MINUS, want=want)
    # 4. colour temperature "-" four times: Warm 4 -> Balanced, cool up and warm down at every step
    for want in ((28, 175), (59, 149), (87, 125), (119, 99)):
        light_step(name, *CT_MINUS, want=want)
    settle(name, 1.5)
    shot2 = tmp / 'light-adjusted.png'
    x4emu(name, 'screenshot', shot2)
    golden_check(shot2, 'stock-light-adjusted.png')      # "Brightness 80%", "Color Temp Balanced"
    assert masked_diff(shot, shot2) > 500, 'the panel did not redraw its labels and handles'

    # 5. the stock stores both in NVS user_config straight away
    flash = tmp / 'stock.bin'
    wait_for(lambda: nvs_u8(flash, 'lightBri') == 80, 20, 'lightBri = 80 in NVS')
    assert nvs_u8(flash, 'lightCT') == 50, nvs_u8(flash, 'lightCT')            # 100 = Warm 4, 50 = Balanced

    # 6. the "Light" button switches the frontlight off and on again (and persists lightOn)
    light_step(name, *LIGHT_BTN, want=(0, 0))
    wait_for(lambda: nvs_u8(flash, 'lightOn') == 0, 20, 'lightOn = 0 in NVS')
    light_step(name, *LIGHT_BTN, want=(119, 99))
    wait_for(lambda: nvs_u8(flash, 'lightOn') == 1, 20, 'lightOn = 1 in NVS')

    # 7. a tap on the dimmed page closes the panel and Home is back, pixel for pixel
    tap(name, *BACKDROP)
    settle(name, 2)
    back = tmp / 'light-closed.png'
    x4emu(name, 'screenshot', back)
    golden_check(back, 'stock-home.png', why=': Home did not come back after closing the light panel')
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']


def home_pad(name, wait=20):
    """Press the Home pad once and return (state before, state after) once a refresh followed."""
    before = state(name)
    r = x4emu(name, 'home', '--wait', wait, check=False)
    settle(name, 2, 120)
    after = state(name)
    assert after['refresh_count'] > before['refresh_count'], f'no repaint after the Home pad: {r.stdout.strip()} {r.stderr.strip()}'
    assert after['gt911']['key_reads'] > before['gt911']['key_reads'], 'the stock never read the GT911 key byte'
    return after


@pytest.mark.parametrize('stock', [{'icount': SKIP_IDLE}], indirect=True, ids=['skip_idle'])
def test_stock_home_pad_acts_as_back(stock):
    """The capacitive Home pad is a GT911 touch key: the stock reads status 0x814E, sees HaveKey
    (bit 4) with no points, then reads the key-value byte right after the point records
    (0x814F + 8 * count; bit 0 = key 1) -- one byte at 0x814F while the pad alone is down -- and
    acts on it. Before the model served that byte the stock read the frames and did nothing
    (docs/log.md 2026-09-06 S1). In the stock the pad is *Back*, one level: from the nav menu it
    returns to the page below (Home, pixel-identical); from the reader it returns to the screen the
    book was opened from (All Files), unlike the reading menu's back arrow, which goes to the
    bookshelf (`test_stock_screens_walk`)."""
    name, tmp = stock
    boot_to_home(name)
    # 1. nav menu open -> Home pad -> the Home screen, pixel for pixel outside the status bar
    step(name, *HAMBURGER, 'stock-menu.png', cols=MENU_COLS)
    home_pad(name)
    shot = tmp / 'after-home-from-menu.png'
    x4emu(name, 'screenshot', shot)
    golden_check(shot, 'stock-home.png', why=': the Home pad from the nav menu did not bring Home back')
    # 2. inside the book (menu -> All Files -> first row, each screen checked) -> Home pad -> All Files
    step(name, *HAMBURGER, 'stock-menu.png', cols=MENU_COLS)
    step(name, 245, 150, 'stock-all-files.png', quiet=2)
    reader = step(name, 250, 320, 'stock-reader-page1.png', cols=READER_COLS, wait=30, quiet=3)
    home_pad(name)
    back = tmp / 'after-home-from-reader.png'
    x4emu(name, 'screenshot', back)
    assert masked_diff(back, reader) > 1000, 'still on the reader page after the Home pad'
    golden_check(back, 'stock-all-files.png')      # back to where the book was opened from, not the bookshelf


BASE_EPOCH = 1614834367     # 2021-03-04 05:06:07 UTC, a Thursday


def bcd(v):
    return ((v >> 4) & 0xF) * 10 + (v & 0xF)


@pytest.mark.parametrize('stock', [{'icount': SKIP_IDLE, 'trace_i2c': True,
                                    'extra': ['-global', f'driver=x4pro.pcf8563,property=base-epoch,value={BASE_EPOCH}']}],
                         indirect=True, ids=['pinned_rtc'])
def test_stock_rtc_pinned_by_base_epoch(stock):
    """`-global driver=x4pro.pcf8563,property=base-epoch,value=N` seeds the BM8563 with N seconds
    since 1970 instead of the host's wall clock (0 = host time, the default), so a run's clock is
    reproducible. The stock reads the RTC once at boot (register pointer 0x02, seven BCD bytes:
    seconds, minutes, hours, day, weekday, month, year); the I2C trace shows what it got."""
    name, tmp = stock
    st = boot_to_home(name)
    assert st['rtc']['reads'] >= 1, st['rtc']
    rows = [json.loads(ln) for ln in open(tmp / 'i2c.jsonl')]
    reads = [rows[i + 1]['r'].split() for i in range(len(rows) - 1)
             if rows[i]['addr'] == '0x51' and rows[i]['rw'] == 'w' and rows[i]['w'] == '02'
             and rows[i + 1]['addr'] == '0x51' and rows[i + 1]['rw'] == 'r']
    assert reads, 'no BM8563 time read in the I2C trace'
    b = [int(x, 16) for x in reads[0][:7]]
    t = datetime.datetime(2000 + bcd(b[6]), bcd(b[5] & 0x1F), bcd(b[3] & 0x3F), bcd(b[2] & 0x3F), bcd(b[1] & 0x7F),
                          bcd(b[0] & 0x7F), tzinfo=datetime.timezone.utc)
    got = int(t.timestamp())
    assert BASE_EPOCH <= got <= BASE_EPOCH + 600, (t.isoformat(), reads[0])
    assert b[4] == 4, f'weekday byte {b[4]} (Thursday = 4)'
