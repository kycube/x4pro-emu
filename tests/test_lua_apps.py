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
otherwise (CI never has them). The flash copy is redacted and deleted (tests/stockkit.py).
"""
import os
import pytest
from conftest import x4emu, ROOT
from stockkit import (APP_CARD, GOLDEN_DIR, HAMBURGER, LUA_APPS_ROW, MENU_COLS, SKIP_IDLE,  # noqa: F401
                      booted, boot_to_home, clean_console, masked_diff, state, step, stock_card)

HELLO = os.path.join(ROOT, 'tests', 'data', 'hello-app')


@pytest.fixture
def stock_lua(tmp_path, request, stock_card):
    """`test_stock.stock` with the `lua-apps-row` patch and `tests/data/hello-app` installed on the
    card by `tools/xtapp.py`. Yields (instance name, tmp_path)."""
    with booted(request, tmp_path, stock_card, patches=('lua-apps-row',), apps=(HELLO,), icount=SKIP_IDLE) as s:
        assert s.apps[0]['card_dir'] == '/XTApps/hello', s.apps
        yield s.name, s.tmp


def test_stock_runs_a_lua_app(stock_lua):
    """The patched menu -> the app list -> our script painting the screen."""
    name, tmp = stock_lua
    boot_to_home(name)

    # 1. the sixth row exists only because of the patch (five rows without it: tests/golden/stock-menu.png)
    menu = step(name, *HAMBURGER, 'stock-menu-lua.png', cols=MENU_COLS, quiet=2)
    assert masked_diff(menu, os.path.join(GOLDEN_DIR, 'stock-menu.png'), MENU_COLS) > 100, \
        'the patched menu looks like the unpatched one'

    # 2. the Lua Apps page (title "Extensions") lists the app: the container parsed and the
    #    manifest's display_name came off the card
    step(name, *LUA_APPS_ROW, 'stock-lua-apps.png', quiet=2)

    # 3. the script runs and owns the frame
    step(name, *APP_CARD, 'stock-lua-hello.png', quiet=2)

    # 4. a clean run: the panel took no unknown command, the script raised nothing, nothing reset
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']
    log = clean_console(name, 'running the Lua app')
    # 5. and the app's own log line is nowhere: ctx.log.info is a no-op in 7.2.4 (see the module
    #    docstring). If this ever fires, the host started logging -- update docs/lua-apps.md.
    uart = x4emu(name, 'log', '--file', 'uart0.log').stdout
    assert 'hello-lua' not in log and 'hello-lua' not in uart, \
        'ctx.log.info reached the console after all -- docs/lua-apps.md says it never does'
