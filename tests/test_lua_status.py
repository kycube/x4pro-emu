"""The two status screens of `apps/` running on the patched stock xteink_app 7.2.4.

Same route as `tests/test_lua_apps.py` -- `tools/stockpatch.py apply ... lua-apps-row` puts a sixth
**Lua Apps** row in the nav menu, `tools/xtapp.py install` puts an app directory on the card -- but
with two real screens on it instead of the hello sample, and with the RTC pinned so their clocks are
reproducible:

  Home -> hamburger -> Lua Apps -> the list, now with both apps  (`stock-lua-status-list.png`)
                    -> "Status (Typographic)"                    (`stock-lua-status-typographic.png`)
                    -> Back, Exit, "Status (Panels)"             (`stock-lua-status-panels.png`)

**What is pinned and what is masked.** `-global driver=x4pro.pcf8563,property=base-epoch,value=N`
seeds the RTC with a fixed instant (`tests/test_stock.py::test_stock_rtc_pinned_by_base_epoch`), so
the date both apps print is a constant and is compared pixel for pixel. Three things still move
between runs and are masked instead, by `diff_masked` below:

  * the HH:MM digits -- the stock reads the RTC a second or two into the boot and its clock then runs
    on guest time, so the displayed minute depends on how long the walk to the app took;
  * the uptime value, for the same reason;
  * the Lua heap value (`ctx.gc:count()`), which depends on where the collector happens to be.

Each masked band is a range of *landscape columns*, which are portrait *rows*: the panel screenshot
is the 480x800 portrait page rotated, so `test_stock.masked_diff`'s "columns 0..59 are the status
bar" is the same statement as "the status bar is the top 60 rows of the page". `masked_diff` can
only cut a prefix off, so `diff_masked` here blanks arbitrary bands in both images first. Every
masked band is also asserted to *contain ink*, so an app that stopped drawing its clock still fails.

Needs the device dump and the device card's files like the rest of `tests/test_stock.py`, and skips
otherwise (CI never has them). The patched flash copy lives in pytest's tmp_path only -- it still
carries the owner's WiFi credentials, CLAUDE.md rule 6.
"""
import os, shutil, subprocess, sys
import pytest
from conftest import x4emu, ROOT, PY, instance_name

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import xtapp                                                                   # noqa: E402
from test_stock import (DUMP, PANEL_W, STATUS_BAR_COLS, boot_to_home,          # noqa: E402
                        settle, state, stock_card, tap)                        # noqa: F401

APPS = os.path.join(ROOT, 'apps')
TYPO = os.path.join(APPS, 'status-typographic')      # -> /XTApps/status1
PANELS = os.path.join(APPS, 'status-panels')         # -> /XTApps/status2

HAMBURGER = (88, 38)                 # the nav-menu icon on Home
LUA_APPS_ROW = (585, 150)            # the sixth row, the one the patch adds
CARD_1 = (225, 355)                  # the left app card on the Lua Apps page
CARD_2 = (225, 125)                  # the right one
EXIT_BUTTON = (492, 144)             # "Exit" in the "Exit app ...?" dialog the Back pad raises

# 2026-03-17 14:41:00 UTC. The stock shows it in the device's local zone; the point is only that it
# is the same instant on every run, and far enough from midnight that no date can roll over.
BASE_EPOCH = 1773758460

# landscape columns == portrait rows; see the module docstring
CLOCK_A = (150, 292)                 # apps/status-typographic: the seven-segment HH:MM
LIVE_A = [CLOCK_A, (586, 620), (718, 752)]        # + the uptime row and the Lua heap row
CLOCK_B = (130, 252)                 # apps/status-panels: the clock card's digits
LIVE_B = [CLOCK_B, (524, 562)]                    # + the UPTIME / LUA HEAP card values (one row)


@pytest.fixture
def stock_status(tmp_path, request, stock_card):
    """`test_lua_apps.stock_lua` with both status apps and a pinned RTC: a scratch copy of the dump
    (`net_en` 0, `lua-apps-row` applied), a copy of the device card with apps/status-typographic and
    apps/status-panels installed, and the BM8563 seeded with BASE_EPOCH. Yields (name, tmp_path)."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    if not shutil.which('mcopy'):
        pytest.skip('mtools not available')
    name = instance_name(request.node.name, request.node.nodeid)
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    for cmd in (['nvsedit.py', str(flash), 'set-u8', 'user_config', 'net_en', '0'],
                ['stockpatch.py', 'apply', str(flash), 'lua-apps-row']):
        subprocess.run([PY, os.path.join(ROOT, 'tools', cmd[0]), *cmd[1:]],
                       check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    for app, where in ((TYPO, '/XTApps/status1'), (PANELS, '/XTApps/status2')):
        rep = xtapp.install(str(sd), app)
        assert rep['card_dir'] == where and 'app.xtapp' in rep['files'], rep
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd),
          '--trace-epd', str(tmp_path / 'epd.jsonl'), '--boot-hold-power',
          '--', '-global', f'driver=x4pro.pcf8563,property=base-epoch,value={BASE_EPOCH}')
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def _px(im):
    """The pixels of a mode-'1' image; getdata() is deprecated from Pillow 11 on (test_stock does
    the same dance)."""
    return im.get_flattened_data() if hasattr(im, 'get_flattened_data') else im.getdata()


def diff_masked(shot, golden, cols=(STATUS_BAR_COLS, PANEL_W), bands=()):
    """Pixels differing in landscape columns cols[0]..cols[1]-1 with `bands` (pairs of landscape
    columns = portrait rows) blanked in both images first. `test_stock.masked_diff` with the extra
    ability to cut a band out of the middle."""
    from PIL import Image, ImageChops, ImageDraw
    a = Image.open(shot).convert('1')
    b = Image.open(golden).convert('1')
    for im in (a, b):
        d = ImageDraw.Draw(im)
        for c0, c1 in bands:
            d.rectangle([c0, 0, c1 - 1, im.height - 1], fill=1)     # 1 = white in mode '1'
    box = (cols[0], 0, min(cols[1], a.width), a.height)
    d = ImageChops.difference(a.crop(box), b.crop(box))
    return sum(1 for v in _px(d) if v)


def ink_in(shot, bands):
    """Black pixels inside `bands`: a masked band that went blank is a broken app, not a pass."""
    from PIL import Image
    im = Image.open(shot).convert('1')
    return [sum(1 for v in _px(im.crop((c0, 0, c1, im.height))) if v == 0) for c0, c1 in bands]


def tap_to(name, xy, golden, shot, cols=(STATUS_BAR_COLS, PANEL_W), bands=(), tries=3, quiet=2):
    """Tap, settle, screenshot and compare against tests/golden/GOLDEN (created from the shot when
    missing). A tap the stock dropped, or one that left the previous screen up, is repeated once --
    the same known flake `test_lua_apps.tap_to` and `test_stock.step` guard against."""
    g = os.path.join(ROOT, 'tests', 'golden', golden)
    n = None
    for attempt in range(tries):
        tap(name, *xy, quiet=quiet)
        settle(name, quiet, 120)
        x4emu(name, 'screenshot', str(shot))
        if not os.path.exists(g):
            shutil.copyfile(shot, g)
            print(f'golden created: {g}')
            return shot
        n = diff_masked(shot, g, cols, bands)
        if n == 0:
            return shot
        print(f'tap {xy}: {n} pixels differ from {golden}, retrying')
    raise AssertionError(f'tap {xy} did not reach tests/golden/{golden} in {tries} tries '
                         f'({n} pixels differ outside {list(bands)}); the shot is {shot}')


def back_to_list(name, tmp, tries=3):
    """Out of a running app and back to the Lua Apps page. The Back pad does not leave the app on
    its own: it raises the stock's `Exit app "<app_id>"?` dialog (Continue | Exit), so the exit is
    two inputs, and the whole pair is retried against the list golden.

    Neither input asks `--wait` for a repaint. The stock ignores input while it paints, and on a
    loaded host (the desk Mac runs the owner's own applications) that made `home --wait` fail, while
    *retrying the pad alone* is the wrong repair: if the first press did raise the dialog, a second
    one dismisses it and the Exit tap then lands on the app. Only the outcome is worth asserting, so
    press, settle, tap Exit, settle, and compare -- a whole attempt at a time."""
    g = os.path.join(ROOT, 'tests', 'golden', 'stock-lua-status-list.png')
    shot = tmp / 'back.png'
    n = None
    for attempt in range(tries):
        x4emu(name, 'home', '--quiet', '2')
        settle(name, 2, 60)
        tap(name, *EXIT_BUTTON, quiet=2)
        settle(name, 2, 60)
        x4emu(name, 'screenshot', str(shot))
        n = diff_masked(shot, g)
        if n == 0:
            return
        print(f'Back + Exit left {n} pixels differing from the app list; starting the exit over')
    raise AssertionError(f'Back + Exit did not return to the Lua Apps list in {tries} tries '
                         f'({n} pixels differ); the shot is {shot}')


def test_stock_runs_both_status_apps(stock_status):
    """Both apps parse, list and paint: the patched menu -> the app list with two cards -> the
    typographic page -> back out through the exit dialog -> the panelled page."""
    name, tmp = stock_status
    boot_to_home(name)
    settle(name, 2, 30)

    # 1. the sixth row (the patch) into the Lua Apps page, which now lists both containers
    tap(name, *HAMBURGER, quiet=2)
    settle(name, 2, 120)
    tap_to(name, LUA_APPS_ROW, 'stock-lua-status-list.png', tmp / 'list.png')

    # 2. variant A: the airy typographic page. Everything but the clock digits, the uptime and the
    #    heap figure is compared pixel for pixel, status bar included -- the app blanks the whole
    #    frame with g:clear(0), so the stock's own painting is not in the golden.
    a = tap_to(name, CARD_1, 'stock-lua-status-typographic.png', tmp / 'typo.png',
               cols=(0, PANEL_W), bands=LIVE_A)
    ink = ink_in(a, LIVE_A)
    assert ink[0] > 2000, f'the seven-segment clock drew almost nothing ({ink[0]} black pixels)'
    assert all(n > 50 for n in ink), f'a masked band is blank: {ink}'

    # 3. out through the "Exit app" dialog and into variant B
    back_to_list(name, tmp)
    b = tap_to(name, CARD_2, 'stock-lua-status-panels.png', tmp / 'panels.png',
               cols=(0, PANEL_W), bands=LIVE_B)
    ink = ink_in(b, LIVE_B)
    assert ink[0] > 2000, f'the clock card drew almost nothing ({ink[0]} black pixels)'
    assert all(n > 50 for n in ink), f'a masked band is blank: {ink}'

    # 4. a clean run: no unknown panel command, no Lua error, no watchdog, nothing reset
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']
    log = x4emu(name, 'log').stdout
    for bad in ('Guru Meditation', 'abort()', 'Backtrace', 'task_wdt',
                'script callback exceeded time budget'):
        assert bad not in log, f'the console shows {bad!r} after running the status apps:\n{log[-2000:]}'
    assert log.count('rst:0x') == 1, 'the ROM banner is there twice: something reset the guest'
