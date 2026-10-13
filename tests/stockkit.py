"""What the stock-firmware emulator tests share: the owner's dumps and card, one way to prepare and
boot a scratch copy, the walk helpers (tap, settle, step, boot_to_home) and the masked golden diff.

Used by test_stock.py, test_stock_capture.py and test_lua_*.py; test_gamebook, test_input_delivery,
test_nvsedit, test_stockstrings and test_xtfont import the same names through test_stock.py.

**The dumps hold the owner's WiFi credentials** (CLAUDE.md rule 6). `stock_flash` redacts every copy
(`tools/nvsedit.py COPY redact`: the credential keys erased, net_en 0) before anything boots it, and
`booted` deletes it when the test ends; the dumps themselves are only ever read.

Coordinates are landscape panel pixels (the 800x480 screenshot) of the stock's portrait UI: portrait
(px, py) is landscape (py, 479 - px), so a band of landscape *columns* is a band of portrait *rows*,
and the portrait status bar (clock, battery) is landscape columns 0..59.
"""
import contextlib, os, shutil, subprocess
from types import SimpleNamespace

import pytest
from conftest import (x4emu, ROOT, PY, TOOLS, count_pixels, copy_image, emu_state, instance_dir,  # noqa: F401
                      instance_name, wait_for)

DUMP = os.path.join(ROOT, 'images', 'device', 'flash-2026-09-06-a.bin')        # stock 7.2.4
DUMP_768 = os.path.join(ROOT, 'images', 'device', 'flash-2026-10-12-a.bin')    # stock 7.6.8
SD_FILES = os.path.join(ROOT, 'images', 'device', 'sd-files')
GOLDEN_DIR = os.path.join(ROOT, 'tests', 'golden')
GOLDEN = os.path.join(GOLDEN_DIR, 'stock-home.png')     # 7.2.4's Home, the empty bookshelf
FAT_OFFSET = 1 << 20                                    # tools/mksd.py: the FAT32 partition at 1 MiB

PANEL_W = 800
STATUS_BAR_COLS = 60
BODY = (STATUS_BAR_COLS, PANEL_W)              # everything but the status bar
WHOLE = (0, PANEL_W)                           # a Lua app blanks the whole frame, status bar included
MENU_COLS = (STATUS_BAR_COLS, PANEL_W - 60)    # the nav menu draws the date in the last 60 columns
READER_COLS = (STATUS_BAR_COLS, PANEL_W - 40)  # the reader draws its clock in the last 40

HAMBURGER = (88, 38)                 # the nav-menu icon on Home
LUA_APPS_ROW = (585, 150)            # the sixth nav row `stockpatch.py ... lua-apps-row` adds (7.2.4)
APP_CARD = (225, 355)                # the first (left) card of the Lua Apps / Extensions list
LIGHT_PULL = (5, 240, 300, 240)      # portrait (240, 5) -> (240, 300): pull the light panel down

# `-icount shift=2,sleep=off` for every stock walk and for the tests that wait on the stock's own
# timers (the 60 s auto-dim, the WiFi start at 14 s and its give-up 8 s later, the restart after a font
# switch): guest time follows the instruction count (4 ns per instruction, about the chip's 240 MHz),
# and while both cores idle in WAITI the clock jumps to the next timer instead of waiting for it in
# real time, so a panel refresh's BUSY time (GC 1326 ms) and a settle's idle seconds pass in a
# fraction of the host time. Every x4emu wait and hold is in guest time (docs/x4emu.md, "Input
# delivery"), so nothing else changes; the firmware sees the device's timings and the same timer
# order, and the goldens are the same pixels (the walks took about half the host time, 2026-10-13).
# Both cores then run on one host thread, so a loaded host cannot stall one core against the other:
# that is what opens stock 7.2.4's own font-slot race (docs/heap.md), and at load 22 one wall-clock
# walk met an "External font failed to load" toast over the menu golden. The default MTTCG, wall-clock mode stays
# covered by test_stock_boots_to_home_lights_and_reacts_to_touch and the other files' stock boots.
# A fixed shift, not `shift=auto`: auto re-rates the instructions to keep guest time near real time,
# so on a loaded host the boot-preflight's sampling loop got fewer iterations into its 600 ms window
# and rejected a held power button (hold=0..450ms decision=2 on 12 boots at load 23; shift=2 and
# shift=3: hold=606/608ms on all 12).
SKIP_IDLE = 'shift=2,sleep=off'


def tool(script, *args):
    subprocess.run([PY, os.path.join(TOOLS, script), *map(str, args)], check=True, stdout=subprocess.DEVNULL)


state = emu_state          # the name test_stock.py's importers use


# --- images ---------------------------------------------------------------------------------------
_card = {}


@pytest.fixture(scope='session')
def stock_card(tmp_path_factory):
    """The device's card as `tools/mksd.py` builds it from images/device/sd-files (its XTData system
    font, XTCache boot plans, .crosspoint state) plus tests/mkepub.py's book at the root, 256 MiB.
    Every test boots its own copy (the stock writes to the card). Built once per pytest process: a
    session fixture imported into several test modules is run once per module that holds it."""
    if not os.path.isdir(SD_FILES):
        pytest.skip('images/device/sd-files not available')
    if not shutil.which('mcopy'):
        pytest.skip('mtools not available')
    if 'img' not in _card:
        from mkepub import make_epub
        d = tmp_path_factory.mktemp('card')
        make_epub(str(d / 'Test Book.epub'))
        img = d / 'sd-device-full.img'
        tool('mksd.py', img, '--size', '256M', '--src', SD_FILES)
        subprocess.run(['mcopy', '-i', f'{img}@@{FAT_OFFSET}', '-o', str(d / 'Test Book.epub'), '::/'], check=True)
        _card['img'] = str(img)
    return _card['img']


def stock_flash(tmp_path, dump=DUMP, net_en=0, patches=(), dev=False):
    """A scratch copy of `dump` at tmp_path/stock.bin, redacted at once (see the module docstring).
    `net_en` 1 turns the stock's WiFi start back on, with a placeholder SSID (no radio is modelled,
    so there is nothing to join either way); `patches` are `tools/stockpatch.py` names; `dev` runs
    `tools/stockdev.py` (developer mode, docs/xic.md). Skips when the dump is not on disk."""
    if not os.path.exists(dump):
        pytest.skip(f'device dump {os.path.basename(dump)} not available')
    flash = tmp_path / 'stock.bin'
    copy_image(dump, flash)
    tool('nvsedit.py', flash, 'redact')
    if net_en:              # redacted + net_en 1 alone: no "phy_init" within 60 s, the stock wants an SSID
        tool('nvsedit.py', flash, 'set-u8', 'user_config', 'net_en', net_en)
        tool('nvsedit.py', flash, 'set-str', 'user_config', 'sta_ssid', 'x4emu-placeholder')
    if patches:
        tool('stockpatch.py', 'apply', flash, *patches)
    if dev:
        tool('stockdev.py', flash)
    return flash


def stock_sd(tmp_path, card, apps=()):
    """A copy of the card at tmp_path/sd.img with each app directory of `apps` installed under
    /XTApps/<app_id> by `tools/xtapp.py` (which raises when app.xtapp did not land); returns the
    card and the install reports."""
    sd = tmp_path / 'sd.img'
    copy_image(card, sd)
    import xtapp                                               # tools/ (conftest puts it on sys.path)
    return sd, [xtapp.install(str(sd), str(app)) for app in apps]


def run_stock(name, flash, sd, trace_epd=None, trace_i2c=None, icount=None, extra=()):
    """`x4emu run` with the power button held from reset (`--boot-hold-power`), so the boot-preflight
    accepts the cold boot (`decision=0`) instead of deep-sleeping."""
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', flash, '--sd', sd, '--boot-hold-power',
          *(['--trace-epd', trace_epd] if trace_epd else []),
          *(['--trace-i2c', trace_i2c] if trace_i2c else []),
          *(['--icount', icount] if icount else []),
          *(['--', *extra] if extra else []))


@contextlib.contextmanager
def booted(request, tmp_path, card, dump=DUMP, net_en=0, patches=(), dev=False, apps=(), prepare=None,
           boot=True, trace_i2c=False, icount=None, extra=()):
    """One test's stock instance: a redacted scratch flash (`stock_flash`), a card copy with `apps`
    (`stock_sd`), `prepare(flash, sd)` for anything else either needs before the boot, and -- unless
    `boot=False` (a test that starts it itself, `run_stock`) -- booted, with the panel commands
    traced to tmp_path/epd.jsonl (and the I2C bus to i2c.jsonl with `trace_i2c`). Yields a
    namespace (name, tmp, flash, sd, apps: the install reports); stops the instance and deletes the
    flash copy at the end."""
    name = instance_name(request.node.name, request.node.nodeid)
    try:
        flash = stock_flash(tmp_path, dump, net_en, patches, dev)
        sd, installed = stock_sd(tmp_path, card, apps)
        if prepare:
            prepare(flash, sd)
        if boot:
            run_stock(name, flash, sd, trace_epd=tmp_path / 'epd.jsonl',
                      trace_i2c=tmp_path / 'i2c.jsonl' if trace_i2c else None, icount=icount, extra=extra)
        else:
            x4emu(name, 'stop', check=False)
        yield SimpleNamespace(name=name, tmp=tmp_path, flash=flash, sd=sd, apps=installed)
    finally:
        x4emu(name, 'stop', check=False)
        (tmp_path / 'stock.bin').unlink(missing_ok=True)


# --- the walk -------------------------------------------------------------------------------------
def settle(name, seconds=1.5, timeout=60):
    """Until the panel has been idle for `seconds` of guest time (at once when it already has)."""
    x4emu(name, 'wait-quiet', '--seconds', seconds, '--timeout', timeout)


def boot_to_home(name):
    """Home painted and the panel idle; returns the state right after the Home paint. Refresh 1 is
    the panel init's full refresh, refresh 2 is Home. Refresh 3 is the status-bar clock, which the
    stock completes at the next wall-clock minute: it pre-sends the old plane right after every
    refresh and sends the new plane + DRF when the minute changes (or at once for a UI event), so
    waiting for a third refresh would take 0..60 s (docs/log.md 2026-09-06, session 4)."""
    x4emu(name, 'wait-text', 'main_task: Returned from app_main()', '--timeout', 60)
    x4emu(name, 'wait-refresh', '--total', 2, '--timeout', 90)
    st = state(name)
    settle(name, 2, 30)
    return st


def state_if(name, pred):
    """The state when `pred(state)` holds, else None: a `wait_for` condition that returns the state."""
    st = state(name)
    return st if pred(st) else None


def tap(name, x, y, quiet=1, wait=20):
    """Tap (x, y) once the panel is idle and require the refresh it triggers (`--wait`). Delivered
    once: the stock's "dropped" taps under a loaded host (docs/log.md sessions 4-8) were releases the
    GT911 model overwrote unread, fixed in the model and in `x4emu`'s input delivery
    (docs/x4emu.md "Input delivery", tests/test_input_delivery.py)."""
    r = x4emu(name, 'tap', x, y, '--quiet', quiet, '--wait', wait, check=False)
    if r.returncode:
        raise AssertionError(f'tap ({x},{y}) drew nothing:\n{r.stdout}\n{r.stderr}')
    return r


def tap_repaints(name, x, y, timeout=20):
    """`tap` that returns (state before, state after the repaint started)."""
    before = state(name)
    tap(name, x, y, wait=timeout)
    return before, state(name)


def masked_diff(a_png, b_png, cols=BODY, bands=()):
    """Pixels differing in landscape columns cols[0]..cols[1]-1, with `bands` (pairs of landscape
    columns) blanked in both images first: a clock or a counter in the middle of the page."""
    from PIL import Image, ImageChops, ImageDraw
    a = Image.open(a_png).convert('1')
    b = Image.open(b_png).convert('1')
    for im in (a, b) if bands else ():
        draw = ImageDraw.Draw(im)
        for c0, c1 in bands:
            draw.rectangle([c0, 0, c1 - 1, im.height - 1], fill=1)     # 1 = white in mode '1'
    box = (cols[0], 0, min(cols[1], a.width), a.height)
    return count_pixels(ImageChops.difference(a.crop(box), b.crop(box)))


def ink_in(png, bands):
    """Black pixels inside each band of landscape columns: a masked band that went blank is a
    broken screen, not a pass."""
    from PIL import Image
    im = Image.open(png).convert('1')
    return [count_pixels(im.crop((c0, 0, c1, im.height)), 0, 0) for c0, c1 in bands]


def golden_check(shot, golden, cols=BODY, bands=(), why=''):
    """`shot` against tests/golden/GOLDEN (created from the shot when missing), 0 pixels apart."""
    g = os.path.join(GOLDEN_DIR, golden)
    if not os.path.exists(g):
        shutil.copyfile(shot, g)
        print(f'golden created: {g}')
    n = masked_diff(shot, g, cols, bands)
    assert n == 0, (f'{os.path.basename(str(shot))} differs from tests/golden/{golden} in {n} pixels '
                    f'(columns {cols[0]}..{cols[1] - 1}' + (f', bands {list(bands)} masked' if bands else '')
                    + f'){why}')


def step(name, x, y, golden=None, cols=BODY, bands=(), wait=20, quiet=1.5, shot=None):
    """Tap (x, y) once the panel is idle, let it settle and check the screen against
    tests/golden/GOLDEN; returns the screenshot (default `.x4emu/NAME/GOLDEN`). The tap's own output
    goes into the failure message, so a tap that drew nothing says so; without a golden the tap must
    have drawn something (`tap`)."""
    if golden is None:
        tap(name, x, y, quiet, wait)
        settle(name, quiet, 120)
        return None
    r = x4emu(name, 'tap', x, y, '--quiet', quiet, '--wait', wait, check=False)
    settle(name, quiet, 120)
    shot = str(shot or os.path.join(instance_dir(name), golden))
    x4emu(name, 'screenshot', shot)
    golden_check(shot, golden, cols, bands, why=f' after tap ({x},{y}): {r.stdout.strip()} {r.stderr.strip()}')
    return shot


def clean_console(name, what):
    """No Lua error, watchdog, panic or reset on the console; returns the console text."""
    log = x4emu(name, 'log').stdout
    for bad in ('Guru Meditation', 'abort()', 'Backtrace', 'task_wdt', 'script callback exceeded time budget'):
        assert bad not in log, f'the console shows {bad!r} after {what}:\n{log[-2000:]}'
    assert log.count('rst:0x') == 1, 'the ROM banner is there twice: something reset the guest'
    return log
