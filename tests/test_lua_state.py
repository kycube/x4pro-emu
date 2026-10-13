"""A Lua app's ctx.state on the stock 7.6.8: saved encrypted on exit, restored on the next start.

The host writes ctx.state to `/XTApps/<app_id>/state.xtsv` when the app is left (Home pad -> Exit)
-- no ctx.save() needed, which is a stub returning false -- and hands it back in on_load. The file
is encrypted: ESP-IDF's DMA-AES runs AES-256 in CTR mode with a 32-bit counter (GCM) on buffers in
PSRAM. Until qemu-patches/0019 the GDMA could not reach PSRAM, AES_STATE never reached DONE, and
the emulated firmware hung on the Exit tap with core 1 spinning on 0x6003A04C (the 2026-10-12
probe, docs/log.md). tests/test_aes.py checks the accelerator itself against a host AES.

The app counts its own visits in ctx.state and draws what on_load got back as horizontal lines
(one per earlier visit) plus a vertical marker when a string and a nested float came back intact,
so the screenshot is read without OCR or goldens. Exit -> re-enter shows the state across a leave
(`test_lua_state_survives_exit_on_stock_768`); a stop and a fresh cold boot from the same flash and
card shows it across a power cycle, i.e. read back from the card and decrypted
(`test_lua_state_survives_reboot_on_stock_768`; two tests on fresh cards, so a parallel run does not
walk both halves one after the other). Boots the owner's 7.6.8 dump as a redacted copy
(tests/stockkit.py); skips without it.
"""
import json, subprocess
import pytest
from conftest import x4emu, emu_log
from stockkit import (APP_CARD, DUMP_768, FAT_OFFSET, HAMBURGER, SKIP_IDLE, booted, boot_to_home,  # noqa: F401
                      run_stock, settle, step, stock_card)

APP_ID = 'state-check'
MINI_APPS = (500, 150)            # 7.6.8's fifth row once /XTApps holds an app (unpatched)
EXIT = (504, 185)                 # "Exit" in the Home pad's 'Exit app "..."?' notice

LINE_Y0, LINE_DY = 300, 20        # visit k (1-based) is a line at portrait y = LINE_Y0 + LINE_DY * k
MARK_X = 30                       # the "types came back intact" marker: a vertical line at x = 30

INDEX_LUA = f"""-- {APP_ID}: what ctx.state brought back, drawn as lines (tests/test_lua_state.py)
local LOADED, INTACT = 0, false
function on_load(c)
  local s = ctx.state
  LOADED = s.visits or 0
  INTACT = s.tag == 'x4emu' and type(s.nest) == 'table' and type(s.nest.a) == 'table'
           and s.nest.a.b == 3.5
end
function on_enter(c)
  ctx.state.visits = LOADED + 1
  ctx.state.tag = 'x4emu'
  ctx.state.nest = {{ a = {{ b = 3.5 }} }}
end
function on_input(c, ev) return true end
function on_draw(c, gg)
  g:clear(0)
  g:text(40, 120, 'restored visits: ' .. LOADED .. (INTACT and ', types intact' or ''))
  for k = 1, LOADED do g:line(60, {LINE_Y0} + {LINE_DY} * k, 420, {LINE_Y0} + {LINE_DY} * k) end
  if INTACT then g:line({MARK_X}, {LINE_Y0}, {MARK_X}, {LINE_Y0} + 200) end
end
"""


def write_app(d):
    d.mkdir()
    (d / 'manifest.json').write_text(json.dumps({
        'app_id': APP_ID, 'display_name': 'State Check', 'version': '1.0', 'author': 'x4pro-emu',
        'description': 'ctx.state across exit and reboot', 'entry': 'index.lua',
        'permissions': ['sys.battery', 'sys.status']}, indent=2))
    (d / 'index.lua').write_text(INDEX_LUA)
    return d


@pytest.fixture
def state_stock(tmp_path, request, stock_card):
    """The redacted 7.6.8 copy and a card with the app on it, not booted (`start` boots it, the
    reboot test twice). Yields (name, tmp_path, flash, card)."""
    app = write_app(tmp_path / APP_ID)
    with booted(request, tmp_path, stock_card, dump=DUMP_768, apps=(app,), boot=False) as s:
        assert s.apps[0]['card_dir'] == f'/XTApps/{APP_ID}', s.apps
        yield s.name, s.tmp, s.flash, s.sd


def start(name, flash, sd):
    run_stock(name, flash, sd, icount=SKIP_IDLE)
    boot_to_home(name)


def open_app(name, from_list=False):
    """Home -> menu -> Mini Apps -> the app; `from_list`: the Extensions list is already on screen
    (where `leave_app` lands), so only the card is tapped. The re-entry used to tap the hamburger and
    the Mini Apps row on that list too: neither draws anything there, and the taps only "passed"
    because the old per-tap retries waited long enough to catch the status bar's minute repaint."""
    if not from_list:
        step(name, *HAMBURGER, quiet=2)
        step(name, *MINI_APPS, quiet=2)
    step(name, *APP_CARD, quiet=2)


def leave_app(name):
    """Home pad -> the Exit notice -> Exit. The Exit tap must repaint (back to the Extensions list):
    with the GDMA unable to reach PSRAM it never did, the save spun on AES_STATE forever."""
    x4emu(name, 'home', '--quiet', '1', '--wait', '20')
    settle(name, 2, 30)
    step(name, *EXIT, quiet=2)


def drawn(name, tmp, label):
    """(lines, marker) the app drew: black rows at portrait x 240 between the line slots, and the
    vertical marker at x MARK_X."""
    from PIL import Image
    p = tmp / f'{label}.png'
    x4emu(name, 'screenshot', str(p))
    im = Image.open(p).rotate(-90, expand=True).convert('L').load()     # upright 480 x 800
    lines = sum(1 for k in range(1, 12) if im[240, LINE_Y0 + LINE_DY * k] < 128)
    marker = all(im[MARK_X, y] < 128 for y in range(LINE_Y0 + 10, LINE_Y0 + 190, 10))
    return lines, marker


def card_file(sd, path, out):
    r = subprocess.run(['mcopy', '-o', '-i', f'{sd}@@{FAT_OFFSET}', f'::{path}', str(out)],
                       capture_output=True, text=True)
    return out.read_bytes() if r.returncode == 0 else None


def no_aes_errors(name):
    err = emu_log(name, 'stderr.log')
    assert '[AES]' not in err, err


def test_lua_state_survives_exit_on_stock_768(state_stock):
    name, tmp, flash, sd = state_stock
    start(name, flash, sd)

    open_app(name)
    assert drawn(name, tmp, 'first') == (0, False), 'a fresh card should hand back an empty ctx.state'
    leave_app(name)                                 # saves {visits=1, ...}: the AES/GDMA path
    open_app(name, from_list=True)
    assert drawn(name, tmp, 'reentered') == (1, True), 'ctx.state did not come back after Exit'
    no_aes_errors(name)


def test_lua_state_survives_reboot_on_stock_768(state_stock):
    name, tmp, flash, sd = state_stock
    start(name, flash, sd)

    open_app(name)
    leave_app(name)                                 # saves {visits=1, ...}
    x4emu(name, 'stop')
    blob = card_file(sd, f'/XTApps/{APP_ID}/state.xtsv', tmp / 'state.xtsv')
    assert blob is not None, f'no /XTApps/{APP_ID}/state.xtsv on the card'
    assert blob[:4] == b'XTSV' and b'x4emu' not in blob, 'state.xtsv should be the encrypted container'

    start(name, flash, sd)                          # a cold boot: the state comes back from the card
    open_app(name)
    assert drawn(name, tmp, 'rebooted') == (1, True), 'ctx.state did not survive a power cycle'
    no_aes_errors(name)
