"""Build a Licorice (XTOS 261013) mini-app package and install it through the device's own UI.

`tools/xtapp.py pack --format licorice` wraps a content key (DEK) for the device's firmware ECDH key
and lays out the container Licorice's installer parses, so a package this repo builds is accepted by
the stock installer. The "device key" is a fixed firmware constant, not device-unique, so the wrap is
reproducible off-device (the full reverse-engineering, with addresses, is docs/licorice-apps.md).

This boots the Licorice dump, drops a package built from apps/probe on the card root, and installs it
the way a user would -- side drawer -> All files -> the .xtapp -> "Install this package?" -> OK --
then checks:

  * the "Install this package?" dialog (the installer recognises our file as a mini-app package),
  * the "Installed -- API Probe" toast (the install, including the mandatory DEK unwrap, succeeded),
  * the "DEK1" entry the firmware writes into its DEK-unwrap cache at RAM 0x3fc9e0f8 only when the
    wrapped key verifies -- the direct proof the key wrap was accepted,
  * the installed package at /sdcard/XTApps/probe/app.xtapp.

What this does NOT cover: launching the app. A Licorice app's code stays encrypted inside app.xtapp
and is decrypted at launch; this package is manifest-only (no code section yet), so it installs and
is listed but "Cannot run yet" -- the launch-time decrypt layer is the open item in
docs/licorice-apps.md. Skips without the device dump (like test_licorice.py), without the
`cryptography` package, or without mtools. app_id must be all ASCII letters, so apps/probe is used
(apps/gull-rock's hyphen is rejected by Licorice's manifest validator).
"""
import os
import shutil
import subprocess

import pytest

pytest.importorskip('cryptography')
from stockkit import (DUMP_LICORICE, FAT_OFFSET, SD_FILES, SKIP_IDLE, booted, golden_check,  # noqa: F401,E402
                      settle, tap, tool, x4emu)
from conftest import ROOT                                                       # noqa: E402

import xtapp                                                                    # noqa: E402

APP = os.path.join(ROOT, 'apps', 'probe')      # an all-ASCII-letter app_id, as Licorice requires

HAMBURGER = (94, 33)                 # the side-drawer button on Home
ALL_FILES = (320, 150)               # the drawer's "All files" column
PACKAGE_ROW = (250, 300)             # probe.xtapp, the only file beside the "screenshots" folder
OK_BUTTON = (430, 400)               # the "OK" of the Install dialog
DIALOG_COLS = (360, 560)             # the dialog pill (no clock/file-size text in this band)
TOAST_COLS = (360, 500)              # the "Installed" toast pill
DEK_CACHE = 0x3fc9e0f8               # FUN_4210a908 writes "DEK1"+app_id here on a valid unwrap


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


def _dek_cache(name):
    """The eight bytes at the DEK-unwrap cache, as a bytes object (via `x4emu mem read`)."""
    out = x4emu(name, 'mem', 'read', hex(DEK_CACHE), 8).stdout
    hexes = [tok for tok in out.replace(':', ' ').split() if tok.startswith('0x') and len(tok) == 4]
    return bytes(int(h, 16) for h in hexes[:8])


def test_licorice_package_installs_through_the_ui(request, tmp_path, licorice_card):
    if not os.path.exists(xtapp.LICORICE_IMAGE):
        pytest.skip(f'{xtapp.LICORICE_IMAGE} not available (the Licorice device key source)')

    def prepare(flash, sd):
        # the same path a user takes: `xtapp.py install --format licorice` drops <app_id>.xtapp on
        # the card root, where All files shows it; the device does the install.
        rep = xtapp.install_licorice(str(sd), APP, xtapp.LICORICE_IMAGE)
        assert rep['card_path'] == '/probe.xtapp'

    with booted(request, tmp_path, licorice_card, dump=DUMP_LICORICE, prepare=prepare,
                icount=SKIP_IDLE) as s:
        x4emu(s.name, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(s.name, 2, 60)

        tap(s.name, *HAMBURGER); settle(s.name, 1.2, 60)
        tap(s.name, *ALL_FILES); settle(s.name, 1.2, 60)
        tap(s.name, *PACKAGE_ROW); settle(s.name, 1.2, 60)
        dialog = tmp_path / 'dialog.png'
        x4emu(s.name, 'screenshot', dialog)
        golden_check(dialog, 'licorice-app-install-dialog.png', cols=DIALOG_COLS,
                     why=' (the "Install this package?" dialog for our .xtapp)')

        tap(s.name, *OK_BUTTON); settle(s.name, 2, 60)
        installed = tmp_path / 'installed.png'
        x4emu(s.name, 'screenshot', installed)
        golden_check(installed, 'licorice-app-installed.png', cols=TOAST_COLS,
                     why=' (the "Installed" toast: the package was accepted)')

        # the firmware caches a successful DEK unwrap as "DEK1" + the app_id; empty means the wrap
        # was rejected (wrong key/endianness/layout) -- the direct proof the key wrap verified.
        cache = _dek_cache(s.name)
        assert cache == b'DEK1prob', f'DEK-unwrap cache is {cache!r}, not the "DEK1"+app_id success mark'

        # and the installer copied the package into /sdcard/XTApps/<app_id>/
        listing = subprocess.run(['mdir', '-i', f'{s.sd}@@{FAT_OFFSET}', '-b', '::/XTApps/probe'],
                                 capture_output=True, text=True).stdout
        assert 'app.xtapp' in listing.lower(), f'/XTApps/probe after install: {listing!r}'
