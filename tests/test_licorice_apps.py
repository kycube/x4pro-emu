"""Build a Licorice (XTOS 261013) mini-app package, install it through the device's own UI, and run it.

`tools/xtapp.py pack --format licorice` wraps a content key (DEK) for the device's firmware ECDH key,
packs every `*.lua` of the app into an "XTCO" code section, encrypts that with AES-128-CTR under the
DEK (iv_code / hmac_code in the manifest) and lays out the container Licorice's installer parses. The
"device key" is a fixed firmware constant, not device-unique, so all of this is reproducible
off-device (the reverse-engineering, with addresses, is docs/licorice-apps.md).

The tests boot the Licorice dump with a package built from apps/probe on the card root and install
it the way a user would -- side drawer -> All files -> the .xtapp -> "Install this package?" -> OK:

  * the "Install this package?" dialog (the installer recognises our file as a mini-app package),
  * the "Installed -- API Probe" toast (the install succeeded: the mandatory DEK unwrap and the
    HMAC-SHA256 check of the encrypted code section),
  * the "DEK1" entry the firmware writes into its DEK-unwrap cache at RAM 0x3fc9e0f8 only when the
    wrapped key verifies -- the direct proof the key wrap was accepted,
  * the installed package at /sdcard/XTApps/probe/app.xtapp.

The last test then runs it, the user's way:

  * the Mini Apps entry is hidden until NVS fw/lua_nav is set, which ten taps (each under 2 s of
    guest time after the last, FUN_4203f650) on Settings -> About -> "Model" do -- an easter egg
    (test_licorice_mini_apps_unlock; the launch test sets the flag in NVS instead); the side drawer
    then lists "Mini Apps" between Cloud and USB mode (the drawer table at DROM 0x3c32c58c),
  * Mini Apps lists "API Probe"; a tap launches it: the Lua host opens app.xtapp, unwraps the DEK,
    checks hmac_code, AES-CTR-decrypts the code section, finds index.lua in the XTCO table and runs it
    (FUN_4210944c, FUN_42107fcc, FUN_42108c44, FUN_420c5fb0) -- page 1 of the probe's API dump,
  * a tap reaches the app's on_input (page 2), the Home pad asks "Leave app" and OK returns to the
    Mini Apps list, with no reset on the console.

Skips without the device dump (like test_licorice.py), without the `cryptography` package, or without
mtools. app_id must be all ASCII letters, so apps/probe is used (apps/gull-rock's hyphen is rejected
by Licorice's manifest validator).
"""
import json
import os
import shutil
import subprocess
import time

import pytest

pytest.importorskip('cryptography')
from stockkit import (BODY, FAT_OFFSET, LICORICE_INK_APP_SHOWN, SD_FILES, SKIP_IDLE, WHOLE, booted,  # noqa: F401,E402
                      golden_check, licorice, licorice_build, settle, state, tap, tool, x4emu)
from conftest import PY, ROOT, TOOLS, emu_log                                  # noqa: E402

import xtapp                                                                    # noqa: E402

APP = os.path.join(ROOT, 'apps', 'probe')      # an all-ASCII-letter app_id, as Licorice requires

HAMBURGER = (94, 33)                 # the side-drawer button on Home
ALL_FILES = (320, 150)               # the drawer's "All files" column
PACKAGE_ROW = (250, 300)             # probe.xtapp, the only file beside the "screenshots" folder
OK_BUTTON = (430, 400)               # the "OK" of the Install dialog
DIALOG_COLS = (360, 560)             # the dialog pill (no clock/file-size text in this band)
TOAST_COLS = (360, 500)              # the "Installed" toast pill
DEK_CACHE = {'v261013': 0x3fc9e0f8,  # FUN_4210a908 writes "DEK1"+app_id here on a valid unwrap
             'v26102901': 0x3fca26d4,    # found by searching DRAM after an install
             'v26110701': 0x3fca3074}
INSTALL_REFRESHES = {'v26110701': 2}  # OK -> "Installed": 26110701 first shows an "Install InkApp" pill

SETTINGS = (580, 149)                # the drawer's "Settings" (portrait 330, 580)
ABOUT = (735, 300)                   # Settings -> About, the seventh row
MODEL_ROW = (295, 380)               # About -> "Model": the row registered with FUN_4203f650
UNLOCK_TAPS = 10                     # FUN_4203f650: the 10th tap within 2000 ms of the previous sets lua_nav
UNLOCK_GAP_MS = 2000
MINI_APPS = (490, 150)               # the drawer's "Mini Apps" row, there once fw/lua_nav != 0
APP_CARD = (215, 355)                # the first card of the Mini Apps grid
APP_BODY = (400, 240)                # anywhere on the running probe: on_input pages forward
LEAVE_OK = (400, 400)                # the "Leave app" dialog's OK
DRAWER_COLS = (60, 720)              # the drawer draws the date at portrait y 730..760
LIST_COLS = (60, 800)                # the Mini Apps page: everything but the status bar (clock)


@pytest.fixture(scope='session')
def licorice_card(tmp_path_factory):
    """The device's card (its fonts and caches) with nothing extra at the root, so All files lists
    exactly the "screenshots" folder and, once installed, the one package. No book, unlike the stock
    tests' card, to keep the file row at a fixed place."""
    if not os.path.isdir(SD_FILES):
        pytest.skip('images/device/sd-files not available')
    if not shutil.which('mcopy'):
        pytest.skip('mtools not available')
    d = tmp_path_factory.mktemp('lcard')
    img = d / 'card.img'
    tool('mksd.py', img, '--size', '256M', '--src', SD_FILES)
    return str(img)


def _dek_cache(name, tag):
    """The eight bytes at the build's DEK-unwrap cache, as a bytes object (via `x4emu mem read`)."""
    out = x4emu(name, 'mem', 'read', hex(DEK_CACHE[tag]), 8).stdout
    hexes = [tok for tok in out.replace(':', ' ').split() if tok.startswith('0x') and len(tok) == 4]
    return bytes(int(h, 16) for h in hexes[:8])


def _prepare(flash, sd):
    """The same path a user takes: `xtapp.py install --format licorice` drops <app_id>.xtapp (with
    its encrypted code section) on the card root, where All files shows it; the device installs it."""
    rep = xtapp.install_licorice(str(sd), APP, xtapp.LICORICE_IMAGE)
    assert rep['card_path'] == '/probe.xtapp' and rep['code_files'] == ['index.lua'], rep


def _shot(name, tmp_path, label):
    png = tmp_path / f'{label}.png'
    x4emu(name, 'screenshot', png)
    return png


def _install_through_the_ui(s, lic):
    """Bookshelf -> drawer -> All files -> probe.xtapp -> "Install this package?" -> OK, with the
    dialog and the "Installed" toast against their goldens and the DEK-unwrap cache mark."""
    x4emu(s.name, 'wait-refresh', '--total', 1, '--timeout', 90)
    settle(s.name, 2, 60)
    tap(s.name, *HAMBURGER); settle(s.name, 1.2, 60)
    tap(s.name, *ALL_FILES); settle(s.name, 1.2, 60)
    tap(s.name, *PACKAGE_ROW); settle(s.name, 1.2, 60)
    golden_check(_shot(s.name, s.tmp, 'dialog'), lic.golden('licorice-app-install-dialog.png'), cols=DIALOG_COLS,
                 why=' (the "Install this package?" dialog for our .xtapp)')
    before = state(s.name)['refresh_count']
    tap(s.name, *OK_BUTTON)
    x4emu(s.name, 'wait-refresh', '--total', before + INSTALL_REFRESHES.get(lic.tag, 1), '--timeout', 60)
    settle(s.name, 2, 60)
    golden_check(_shot(s.name, s.tmp, 'installed'), lic.golden('licorice-app-installed.png'), cols=TOAST_COLS,
                 why=' (the "Installed" toast: the package was accepted)')
    # the firmware caches a successful DEK unwrap as "DEK1" + the app_id; empty means the wrap was
    # rejected (wrong key/endianness/layout) -- the direct proof the key wrap verified.
    cache = _dek_cache(s.name, lic.tag)
    assert cache == b'DEK1prob', f'DEK-unwrap cache is {cache!r}, not the "DEK1"+app_id success mark'


def test_licorice_package_installs_through_the_ui(request, tmp_path, licorice_card, licorice):
    if not os.path.exists(xtapp.LICORICE_IMAGE):
        pytest.skip(f'{xtapp.LICORICE_IMAGE} not available (the Licorice device key source)')
    with booted(request, tmp_path, licorice_card, dump=licorice.dump, prepare=_prepare,
                icount=SKIP_IDLE) as s:
        _install_through_the_ui(s, licorice)
        # and the installer copied the package into /sdcard/XTApps/<app_id>/, verbatim (the code
        # section stays encrypted at rest: it is decrypted in RAM at every launch)
        listing = subprocess.run(['mdir', '-i', f'{s.sd}@@{FAT_OFFSET}', '-b', '::/XTApps/probe'],
                                 capture_output=True, text=True).stdout
        assert 'app.xtapp' in listing.lower(), f'/XTApps/probe after install: {listing!r}'
        back = tmp_path / 'installed.xtapp'
        subprocess.run(['mcopy', '-i', f'{s.sd}@@{FAT_OFFSET}', '-o', '::/XTApps/probe/app.xtapp',
                        str(back)], check=True)
        subprocess.run(['mcopy', '-i', f'{s.sd}@@{FAT_OFFSET}', '-o', '::/probe.xtapp',
                        str(tmp_path / 'source.xtapp')], check=True)
        assert back.read_bytes() == (tmp_path / 'source.xtapp').read_bytes(), \
            'the installed app.xtapp is not the package byte for byte'


def _lua_nav(flash):
    """True once NVS fw/lua_nav = 1 is in the flash image (the emulator writes NVS through to it)."""
    out = subprocess.run([PY, os.path.join(TOOLS, 'nvsedit.py'), str(flash), 'list'],
                         capture_output=True, text=True, check=True).stdout
    return 'fw/lua_nav (u8) = 1' in out


def _prepare_unlocked(flash, sd):
    """`_prepare` plus NVS fw/lua_nav = 1, what the About easter egg writes (test_licorice_mini_apps_
    unlock proves that), so the launch test reaches Mini Apps without ten timed taps."""
    _prepare(flash, sd)
    tool('nvsedit.py', flash, 'set-u8', 'fw', 'lua_nav', 1)      # 26110701 no longer reads it: harmless there


def test_licorice_mini_apps_unlock(request, tmp_path, licorice_card, licorice):
    """Mini Apps is hidden until NVS fw/lua_nav is set: ten taps on Settings -> About -> "Model", each
    under 2000 ms of guest time after the last (FUN_4203f650). The taps are replayed in one process
    (`x4emu replay --no-wait`), so no host pause stretches a gap, and the replay reports each tap's
    guest time. The burst starts right after the status bar's minute repaint has finished: a tap that
    lands while the panel repaints is read and dropped, and that repaint's place relative to the burst
    would otherwise follow the host's load. One burst, no retry. The drawer then lists Mini Apps."""
    if licorice.tag in LICORICE_INK_APP_SHOWN:
        pytest.skip(f'{licorice.tag} lists InkApp with no easter egg (no lua_nav in the image; test_licorice_walk\'s '
                    'drawer golden shows it)')
    with booted(request, tmp_path, licorice_card, dump=licorice.dump, icount=SKIP_IDLE) as s:
        n = s.name
        x4emu(n, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(n, 2, 60)
        assert not _lua_nav(s.flash), 'fw/lua_nav is set before the easter egg'
        tap(n, *HAMBURGER); settle(n, 1.2, 60)
        tap(n, *SETTINGS); settle(n, 1.5, 60)
        tap(n, *ABOUT); settle(n, 1.5, 60)
        x4emu(n, 'wait-refresh', '--count', 1, '--timeout', 120)     # the status bar's next minute
        settle(n, 1.5, 60)
        journal = tmp_path / 'unlock.jsonl'
        step = {'t_ms': None, 'cmd': 'tap',
                'args': {'x': MODEL_ROW[0], 'y': MODEL_ROW[1], 'ms': 60, 'wait': 0, 'quiet': 0}}
        journal.write_text(''.join(json.dumps(step) + '\n' for _ in range(UNLOCK_TAPS)))
        rep = json.loads(x4emu(n, '--json', 'replay', journal, '--no-wait').stdout)
        times = [r['guest_ms'] for r in rep['replayed']]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert len(times) == UNLOCK_TAPS and max(gaps) < UNLOCK_GAP_MS, \
            f'the unlock taps were {gaps} ms apart; FUN_4203f650 needs each under {UNLOCK_GAP_MS} ms'
        settle(n, 1.5, 60)
        t0 = time.time()
        while time.time() - t0 < 10 and not _lua_nav(s.flash):
            time.sleep(0.5)
        assert _lua_nav(s.flash), f'ten taps on About -> Model did not set fw/lua_nav (gaps {gaps} ms)'
        x4emu(n, 'home', '--quiet', 1, '--wait', 20); settle(n, 1, 60)     # About -> Settings
        x4emu(n, 'home', '--quiet', 1, '--wait', 20); settle(n, 1, 60)     # Settings -> Bookshelf
        tap(n, *HAMBURGER); settle(n, 1.2, 60)
        golden_check(_shot(n, tmp_path, 'drawer'), licorice.golden('licorice-app-drawer.png'), cols=DRAWER_COLS,
                     bands=licorice.home_bands,
                     why=' (the drawer with "Mini Apps", once the easter egg set fw/lua_nav)')


def test_licorice_app_runs_from_the_mini_apps_list(request, tmp_path, licorice_card, licorice):
    """Install apps/probe through the UI, open Mini Apps (fw/lua_nav set in NVS before the boot, as the
    easter egg would), launch it, page it with a tap, leave it: each screen against its golden
    (tests/golden/licorice-app-*.png)."""
    if not os.path.exists(xtapp.LICORICE_IMAGE):
        pytest.skip(f'{xtapp.LICORICE_IMAGE} not available (the Licorice device key source)')
    with booted(request, tmp_path, licorice_card, dump=licorice.dump, prepare=_prepare_unlocked,
                icount=SKIP_IDLE) as s:
        n = s.name
        _install_through_the_ui(s, licorice)
        x4emu(n, 'wait-guest-ms', 3000)                        # the toast times out
        x4emu(n, 'home', '--quiet', 1, '--wait', 20)           # All files -> Bookshelf
        settle(n, 1.5, 60)
        tap(n, *HAMBURGER); settle(n, 1.2, 60)                 # the drawer (its golden: the unlock test)
        tap(n, *MINI_APPS); settle(n, 1.5, 60)
        golden_check(_shot(n, tmp_path, 'list'), licorice.golden('licorice-app-list.png'), cols=LIST_COLS,
                     why=' (Mini Apps listing the installed "API Probe")')

        # the launch: open, unwrap, HMAC, AES-CTR, XTCO, luaL_loadbufferx(index.lua), on_draw
        tap(n, *APP_CARD, wait=30); settle(n, 2, 90)
        golden_check(_shot(n, tmp_path, 'running'), licorice.golden('licorice-app-running.png'), cols=WHOLE,
                     why=' (the probe running: page 1 of its API dump, drawn by index.lua)')
        tap(n, *APP_BODY, wait=30); settle(n, 2, 90)
        golden_check(_shot(n, tmp_path, 'page2'), licorice.golden('licorice-app-page2.png'), cols=WHOLE,
                     why=' (a tap reached on_input: page 2)')

        x4emu(n, 'home', '--quiet', 1, '--wait', 20); settle(n, 1.5, 60)
        golden_check(_shot(n, tmp_path, 'leave'), licorice.golden('licorice-app-leave.png'), cols=WHOLE,
                     why=' (the Home pad in an app: "Leave app -- Leave this mini app?")')
        tap(n, *LEAVE_OK); settle(n, 1.5, 60)
        golden_check(_shot(n, tmp_path, 'left'), licorice.golden('licorice-app-list.png'), cols=LIST_COLS,
                     why=' (OK leaves the app, back on the Mini Apps list)')
        log = emu_log(n)
        assert log.count('rst:0x') == 1, f'the guest reset during the run:\n{log[-1500:]}'


def _P(px, py):
    """A portrait point of Licorice's UI as the landscape panel pixel x4emu takes."""
    return py, 479 - px


def _hold(n, px, py):
    x4emu(n, 'tap', *_P(px, py), '--ms', 1500, '--quiet', 1, '--wait', 20)
    settle(n, 2, 60)


def _at(n, px, py):
    tap(n, *_P(px, py))
    settle(n, 1.5, 60)


def test_licorice_ink_app_folders(request, tmp_path, licorice_card):
    """26110701's InkApp page groups apps in folders: two packages installed through All files (apps/probe
    and a copy named "Second Probe"), a held icon's menu (Move / Move to folder / Uninstall), Move to
    folder -> New folder makes "Folder 1", the second app moves into it, the folder opens on both, and
    the folder's own menu (Move / Rename / Ungroup) ungroups it back to two icons."""
    lic = licorice_build('v26110701')
    if not os.path.exists(xtapp.LICORICE_IMAGE):
        pytest.skip(f'{xtapp.LICORICE_IMAGE} not available (the Licorice device key source)')
    second = tmp_path / 'second'
    shutil.copytree(APP, second)
    manifest = json.loads((second / 'manifest.json').read_text())
    manifest.update(app_id='second', display_name='Second Probe')
    (second / 'manifest.json').write_text(json.dumps(manifest, indent=2))

    def prepare(flash, sd):
        for app in (APP, str(second)):
            xtapp.install_licorice(str(sd), app, xtapp.LICORICE_IMAGE)

    def grid(name, why):
        golden_check(_shot(n, tmp_path, name), lic.golden(f'licorice-{name}.png'), cols=BODY, why=why)

    with booted(request, tmp_path, licorice_card, dump=lic.dump, prepare=prepare, icount=SKIP_IDLE) as s:
        n = s.name
        x4emu(n, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(n, 2, 60)
        tap(n, *HAMBURGER)
        tap(n, *lic.drawer('All files'))
        settle(n, 1.5, 60)
        for row in (252, 396):              # probe.xtapp, then second.xtapp below the new XTApps folder
            _at(n, 250, row)
            before = state(n)['refresh_count']
            tap(n, *OK_BUTTON)
            x4emu(n, 'wait-refresh', '--total', before + 1, '--timeout', 60)   # (the second install skips the pill)
            settle(n, 2, 60)
            _at(n, 240, 650)                # the "Installed" toast stays until a tap, which only dismisses it
        listing = subprocess.run(['mdir', '-i', f'{s.sd}@@{FAT_OFFSET}', '-b', '::/XTApps'],
                                 capture_output=True, text=True).stdout.lower()
        assert 'probe' in listing and 'second' in listing, f'/XTApps after the installs: {listing!r}'
        tap(n, *HAMBURGER)
        tap(n, *lic.drawer('InkApp'))
        settle(n, 1.5, 60)
        grid('inkapp-grid', ' (API Probe and Second Probe)')
        _hold(n, 93, 181)                                      # API Probe
        grid('inkapp-app-menu', ' (Move / Move to folder / Uninstall / Back)')
        _at(n, 220, 440)                                       # Move to folder
        _at(n, 190, 469)                                       # New folder
        grid('inkapp-folder-new', ' (Folder 1 holding API Probe, Second Probe beside it)')
        _hold(n, 240, 181)                                     # Second Probe
        _at(n, 220, 440)                                       # Move to folder
        _at(n, 170, 426)                                       # Folder 1
        grid('inkapp-folder-both', ' (Folder 1 alone, two apps in it)')
        _at(n, 93, 181)                                        # open it
        grid('inkapp-in-folder', ' (Folder 1: both apps)')
        x4emu(n, 'home', '--quiet', 1, '--wait', 20)           # Back
        settle(n, 1.5, 60)
        _hold(n, 93, 181)                                      # Folder 1
        grid('inkapp-folder-menu', ' (Move / Rename / Ungroup / Back)')
        _at(n, 152, 499)                                       # Ungroup
        grid('inkapp-grid', ' (ungrouped: the two icons again)')
