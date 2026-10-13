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
between runs and are masked instead:

  * the HH:MM digits -- the stock reads the RTC a second or two into the boot and its clock then runs
    on guest time, so the displayed minute depends on how long the walk to the app took;
  * the uptime value, for the same reason;
  * the Lua heap value (`ctx.gc:count()`), which depends on where the collector happens to be.

Each masked band is a range of *landscape columns*, which are portrait *rows*: the panel screenshot
is the 480x800 portrait page rotated, so "columns 0..59 are the status bar" is the same statement as
"the status bar is the top 60 rows of the page"; `stockkit.masked_diff` blanks the bands in both
images first. Every masked band is also asserted to *contain ink*, so an app that stopped drawing its
clock still fails.

Needs the device dump and the device card's files like the rest of `tests/test_stock.py`, and skips
otherwise (CI never has them). The flash copy is redacted and deleted (tests/stockkit.py).
"""
import os
import pytest
from conftest import x4emu, ROOT
from stockkit import (APP_CARD, HAMBURGER, LUA_APPS_ROW, SKIP_IDLE, WHOLE, booted, boot_to_home,  # noqa: F401
                      clean_console, ink_in, settle, state, step, stock_card)

APPS = os.path.join(ROOT, 'apps')
TYPO = os.path.join(APPS, 'status-typographic')      # -> /XTApps/status-typographic
PANELS = os.path.join(APPS, 'status-panels')         # -> /XTApps/status-panels

CARD_1 = APP_CARD                    # the left app card on the Lua Apps page
CARD_2 = (225, 125)                  # the right one
EXIT_BUTTON = (492, 144)             # "Exit" in the "Exit app ...?" dialog the Back pad raises

# 2026-03-17 14:41:00 UTC. The stock shows it in the device's local zone; the point is only that it
# is the same instant on every run, and far enough from midnight that no date can roll over.
BASE_EPOCH = 1773758460

# landscape columns == portrait rows; see the module docstring
CLOCK_A = (150, 292)                 # apps/status-typographic: the seven-segment HH:MM
LIVE_A = [CLOCK_A, (586, 620), (718, 752)]        # + the uptime row and the Lua heap row
CLOCK_B = (128, 232)                 # apps/status-panels: the clock card's digits
LIVE_B = [CLOCK_B, (506, 528)]                    # + the UPTIME / LUA HEAP card values (one row)


@pytest.fixture
def stock_status(tmp_path, request, stock_card):
    """`test_lua_apps.stock_lua` with both status apps and a pinned RTC: the redacted flash copy with
    `lua-apps-row` applied, a copy of the device card with apps/status-typographic and
    apps/status-panels installed, and the BM8563 seeded with BASE_EPOCH. Yields (name, tmp_path)."""
    with booted(request, tmp_path, stock_card, patches=('lua-apps-row',), apps=(TYPO, PANELS), icount=SKIP_IDLE,
                extra=('-global', f'driver=x4pro.pcf8563,property=base-epoch,value={BASE_EPOCH}')) as s:
        assert [r['card_dir'] for r in s.apps] == ['/XTApps/status-typographic', '/XTApps/status-panels'], s.apps
        yield s.name, s.tmp


def back_to_list(name):
    """Out of a running app and back to the Lua Apps page. The Back pad does not leave the app on
    its own: it raises the stock's `Exit app "<app_id>"?` dialog (Continue | Exit), so the exit is
    two inputs, checked together against the list golden. Neither is retried: the lost Back presses
    on a loaded host were the GT911 model wiping an unread release (the stock then saw the pad held,
    a long press), fixed in the model and `x4emu` (tests/test_input_delivery.py); and a second Back
    press would dismiss the dialog the first one raised."""
    x4emu(name, 'home', '--quiet', '2')
    settle(name, 2, 60)
    step(name, *EXIT_BUTTON, 'stock-lua-status-list.png', quiet=2)


def live_bands_inked(shot, bands, what):
    ink = ink_in(shot, bands)
    assert ink[0] > 2000, f'{what} drew almost nothing ({ink[0]} black pixels)'
    assert all(n > 50 for n in ink), f'a masked band is blank: {ink}'


def test_stock_runs_both_status_apps(stock_status):
    """Both apps parse, list and paint: the patched menu -> the app list with two cards -> the
    typographic page -> back out through the exit dialog -> the panelled page."""
    name, tmp = stock_status
    boot_to_home(name)

    # 1. the sixth row (the patch) into the Lua Apps page, which now lists both containers
    step(name, *HAMBURGER, quiet=2)
    step(name, *LUA_APPS_ROW, 'stock-lua-status-list.png', quiet=2)

    # 2. variant A: the airy typographic page. Everything but the clock digits, the uptime and the
    #    heap figure is compared pixel for pixel, status bar included -- the app blanks the whole
    #    frame with g:clear(0), so the stock's own painting is not in the golden.
    a = step(name, *CARD_1, 'stock-lua-status-typographic.png', cols=WHOLE, bands=LIVE_A, quiet=2)
    live_bands_inked(a, LIVE_A, 'the seven-segment clock')

    # 3. out through the "Exit app" dialog and into variant B
    back_to_list(name)
    b = step(name, *CARD_2, 'stock-lua-status-panels.png', cols=WHOLE, bands=LIVE_B, quiet=2)
    live_bands_inked(b, LIVE_B, 'the clock card')

    # 4. a clean run: no unknown panel command, no Lua error, no watchdog, nothing reset
    st = state(name)
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']
    clean_console(name, 'running the status apps')
