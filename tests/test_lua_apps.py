"""A Lua app of ours running on the stock xteink_app 7.2.4, end to end (one emulator test).

The stock has a complete Lua 5.5 scripting host that nothing in the shipped UI can reach: no menu
row enters page 0x09 (`AppsPagePresenter`), because the nav-menu builder appends that slot only
behind a byte nothing ever writes. `tools/stockpatch.py`'s `lua-apps-row` (3 bytes at app offset
0x2ed873, `l8ui a8,a2,172` -> `movi a8,1`) makes the load a constant, and the menu grows a sixth row
**Lua Apps**. The page lists `/sdcard/XTApps/<dir>` -- but only directories whose `app.xtapp`
container opens, which is what `tools/xtapp.py` builds (docs/lua-apps.md).

So this test is the proof of the whole route, in four screens:

  Home -> hamburger -> the six-row menu (`stock-menu-lua.png`, the sixth row is ours)
       -> Lua Apps  -> the app list with "Hello Lua" on it (`stock-lua-apps.png`: the container
                       parsed and the manifest's display_name is on screen)
       -> the app   -> `tests/data/hello-app/index.lua` painting (`stock-lua-hello.png`: rectangle,
                       two lines of text, a rule, a circle and a tap counter, on a frame the script
                       itself blanked with `g:clear(0)`)

The last screen is drawn entirely by our Lua: `g:clear(0)` whitens the whole frame, status bar
included, so nothing of the stock's own painting is left in the golden and the picture is a pure
function of the script.

**The console stays silent.** `ctx.log.info/warn/error` all exist, all return without an error, and
none of them reaches the USB Serial/JTAG console, UART0 or the card -- with or without developer
mode (session 8). The assertion below therefore checks the opposite of what one would hope: the
app's line is *absent*, and it is a failure if a future image starts printing it, at which point
this test and docs/lua-apps.md should be updated. What is asserted positively is that the run is
clean: no Lua error, no watchdog, no panic, no reset.

Needs the device dump and the device card's files like the rest of tests/test_stock.py, and skips
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
                        masked_diff, settle, state, stock_card, tap)           # noqa: F401

HELLO = os.path.join(ROOT, 'tests', 'data', 'hello-app')
HAMBURGER = (88, 38)                 # the nav-menu icon on Home
LUA_APPS_ROW = (585, 150)            # the sixth row: 160/245/330/415/500 are the five stock ones
APP_CARD = (225, 355)                # the "Hello Lua" card on the app list
MENU_COLS = (STATUS_BAR_COLS, PANEL_W - 60)     # the menu draws the date in the last 60 columns


@pytest.fixture
def stock_lua(tmp_path, request, stock_card):
    """`test_stock.stock` with the `lua-apps-row` patch and our app on the card: a scratch copy of
    the dump (`net_en` 0, `tools/stockpatch.py apply ... lua-apps-row`) and a copy of the device
    card with `tests/data/hello-app` installed into /XTApps/hello by `tools/xtapp.py`. Yields
    (instance name, tmp_path)."""
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
    rep = xtapp.install(str(sd), HELLO)
    assert rep['card_dir'] == '/XTApps/hello' and 'app.xtapp' in rep['files'], rep
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd),
          '--trace-epd', str(tmp_path / 'epd.jsonl'), '--boot-hold-power')
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def tap_to(name, xy, golden, shot, cols=(STATUS_BAR_COLS, PANEL_W), tries=3, quiet=2):
    """Tap, settle, screenshot and compare against tests/golden/GOLDEN (created from the shot when
    missing, like `test_stock.golden_check`). A tap the stock dropped, or one that left the previous
    screen up, is repeated once (`test_stock.step`)."""
    g = os.path.join(ROOT, 'tests', 'golden', golden)
    for attempt in range(tries):
        tap(name, *xy, quiet=quiet)
        settle(name, quiet, 120)
        x4emu(name, 'screenshot', str(shot))
        if not os.path.exists(g):
            shutil.copyfile(shot, g)
            print(f'golden created: {g}')
            return shot
        n = masked_diff(shot, g, cols)
        if n == 0:
            return shot
    raise AssertionError(f'tap {xy} did not reach tests/golden/{golden} in {tries} tries '
                         f'({n} pixels differ in columns {cols[0]}..{cols[1] - 1}); the shot is {shot}')


def test_stock_runs_a_lua_app(stock_lua):
    """The patched menu -> the app list -> our script painting the screen."""
    name, tmp = stock_lua
    boot_to_home(name)
    settle(name, 2, 30)

    # 1. the sixth row exists only because of the patch (five rows without it: tests/golden/stock-menu.png)
    menu = tap_to(name, HAMBURGER, 'stock-menu-lua.png', tmp / 'menu.png', cols=MENU_COLS)
    assert masked_diff(menu, os.path.join(ROOT, 'tests', 'golden', 'stock-menu.png'),
                       MENU_COLS) > 100, 'the patched menu looks like the unpatched one'

    # 2. the Lua Apps page (title "Extensions") lists the app: the container parsed and the
    #    manifest's display_name came off the card
    tap_to(name, LUA_APPS_ROW, 'stock-lua-apps.png', tmp / 'apps.png')

    # 3. the script runs and owns the frame
    tap_to(name, APP_CARD, 'stock-lua-hello.png', tmp / 'hello.png')

    # 4. a clean run: the panel took no unknown command, the script raised nothing, nothing reset
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']
    log = x4emu(name, 'log').stdout
    for bad in ('Guru Meditation', 'abort()', 'Backtrace', 'task_wdt',
                'script callback exceeded time budget'):
        assert bad not in log, f'the console shows {bad!r} after running the Lua app:\n{log[-2000:]}'
    assert log.count('rst:0x') == 1, 'the ROM banner is there twice: something reset the guest'
    # 5. and the app's own log line is nowhere: ctx.log.info is a no-op in 7.2.4 (see the module
    #    docstring). If this ever fires, the host started logging -- update docs/lua-apps.md.
    uart = x4emu(name, 'log', '--file', 'uart0.log').stdout
    assert 'hello-lua' not in log and 'hello-lua' not in uart, \
        'ctx.log.info reached the console after all -- docs/lua-apps.md says it never does'
