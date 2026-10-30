"""XTOS Licorice 26102901's new features (docs/stock-licorice.md, "26102901"): the startup passcode,
collections and StarDict dictionary lookup, each walked from a cold boot of the device's redacted dump and
checked screen by screen against its golden (tests/golden/licorice-v26102901-*). 261013 has none of
them; the tests skip without the 26102901 dump. Phone pairing is not pinned: it waits for a phone over
BLE, which is not modelled."""
import os
import subprocess

import pytest

from stockkit import (BODY, FAT_OFFSET, LICORICE_DUMPS, SKIP_IDLE, WHOLE, booted, golden_check, settle, state,
                      stock_card, tap, wait_for, x4emu)  # noqa: F401
from mkstardict import make_stardict

DUMP = LICORICE_DUMPS['v26102901']
HAMBURGER = (94, 33)                 # portrait (446, 94): the ≡ on Home
READER_COLS = (0, 760)               # the reader's footer clock is at portrait y 767..792
DATE_ROW = (165, 200)                # General's "Date & time" value, portrait y 170..195: today's date and time
PIN, WRONG = '1234', '9999'
KEYPAD = {'1': (110, 352), '2': (240, 352), '3': (370, 352), '4': (110, 462), '9': (370, 572)}

pytestmark = pytest.mark.skipif(not os.path.exists(DUMP), reason=f'{DUMP} not available')


def P(px, py):
    """A portrait point of Licorice's UI as the landscape panel pixel x4emu takes."""
    return py, 479 - px


def at(n, px, py, quiet=1.5):
    tap(n, *P(px, py))
    settle(n, quiet, 60)


def hold(n, px, py, ms):
    x4emu(n, 'tap', *P(px, py), '--ms', ms, '--quiet', 1, '--wait', 20)
    settle(n, 2, 60)


def check(n, tmp_path, name, cols=BODY, why='', bands=()):
    png = tmp_path / f'{name}.png'
    x4emu(n, 'screenshot', png)
    golden_check(png, f'licorice-v26102901-{name}.png', cols=cols, bands=bands, why=why)


def keys(n, digits):
    for d in digits:
        at(n, *KEYPAD[d], quiet=1)


def boot(n):
    x4emu(n, 'wait-refresh', '--total', 1, '--timeout', 90)
    settle(n, 2, 60)


def test_licorice_startup_passcode(request, tmp_path, stock_card):
    """Settings → General → Startup passcode: a keypad, the code twice, the row On; then a deep sleep and a
    wake stop at "Enter passcode", a wrong code is refused and the right one opens Home."""
    with booted(request, tmp_path, stock_card, dump=DUMP, icount=SKIP_IDLE) as s:
        n = s.name
        boot(n)
        tap(n, *HAMBURGER)
        at(n, 330, 580)                                        # Settings
        at(n, 140, 250)                                        # General
        at(n, 240, 615)                                        # Startup passcode
        check(n, tmp_path, 'passcode-new', WHOLE, ' ("Enter new passcode")')
        keys(n, PIN)
        check(n, tmp_path, 'passcode-again', WHOLE, ' ("Enter passcode again")')
        keys(n, PIN)
        settle(n, 1.5, 60)
        check(n, tmp_path, 'passcode-on', why=' (General: Startup passcode On)', bands=[DATE_ROW])

        x4emu(n, 'hold', 'power', '--ms', 1500)
        wait_for(lambda: state(n)['sleep']['sleeping'], 30, 'Licorice to deep-sleep', every=0.2)
        refreshes = state(n)['refresh_count']
        x4emu(n, 'press', 'power')
        wait_for(lambda: (v := state(n))['refresh_count'] > refreshes and not v['sleep']['sleeping'], 60,
                 'Licorice to wake and paint')
        settle(n, 2, 60)
        check(n, tmp_path, 'passcode-locked', WHOLE, ' (a wake asks for the passcode)')
        keys(n, WRONG)
        check(n, tmp_path, 'passcode-wrong', WHOLE, ' ("Wrong passcode")')
        keys(n, PIN)
        settle(n, 2, 60)
        check(n, tmp_path, 'passcode-unlocked', why=' (the right code opens Home)')


def test_licorice_collections(request, tmp_path, stock_card):
    """Home → Recent ⌄ → Collections → New collection (the on-screen keyboard, "Collection 1"), then All
    books, a held cover's menu, Add to collection: the book lands in it."""
    with booted(request, tmp_path, stock_card, dump=DUMP, icount=SKIP_IDLE) as s:
        n = s.name
        boot(n)
        at(n, 80, 80)                                          # Recent ⌄
        check(n, tmp_path, 'home-menu', why=' (Recent / All books / Collections, sort, filter)')
        at(n, 240, 202)                                        # Collections
        at(n, 125, 200)                                        # New collection
        check(n, tmp_path, 'collection-name', WHOLE, ' (the keyboard, "Collection 1")')
        at(n, 425, 750)                                        # OK
        check(n, tmp_path, 'collection-empty', why=' ("Empty. Hold a book to add it here")')
        at(n, 100, 80)                                         # Collection 1 ⌄
        at(n, 355, 145)                                        # All books
        hold(n, 85, 220, 1500)                                 # the test book's cover
        check(n, tmp_path, 'book-menu', why=' (Mark as finished / Add to collection / Delete book)')
        at(n, 230, 441)                                        # Add to collection
        at(n, 140, 176)                                        # Collection 1
        settle(n, 6, 60)                                       # the "Added to" toast clears
        check(n, tmp_path, 'collection-added', why=' (Collection 1: 1 book, ticked)')


def test_licorice_dictionary_lookup(request, tmp_path, stock_card):
    """A StarDict ZIP in /dictionaries/ (tests/mkstardict.py, wikdict's layout): Current dictionary lists
    it, a tap imports it; with excerpts moved off long press, lookup takes it, and a held word in the
    test book opens its definition (on page 2; long press because under icount two taps do not land
    inside the double-tap interval, docs/stock-licorice.md)."""
    zip_path = tmp_path / 'emutest.zip'
    make_stardict(str(zip_path))

    def prepare(flash, sd):
        subprocess.run(['mmd', '-i', f'{sd}@@{FAT_OFFSET}', '::/dictionaries'], check=True)
        subprocess.run(['mcopy', '-i', f'{sd}@@{FAT_OFFSET}', str(zip_path), '::/dictionaries/'], check=True)

    with booted(request, tmp_path, stock_card, dump=DUMP, icount=SKIP_IDLE, prepare=prepare) as s:
        n = s.name
        boot(n)
        tap(n, *HAMBURGER)
        at(n, 330, 580)                                        # Settings
        at(n, 140, 640)                                        # Reading
        at(n, 240, 590)                                        # Highlight excerpts
        at(n, 240, 168)                                        # Excerpt method
        at(n, 140, 528)                                        # Off
        at(n, 38, 80)                                          # back to Reading options
        at(n, 240, 697)                                        # Dictionary lookup
        at(n, 240, 275)                                        # Current dictionary
        check(n, tmp_path, 'dictionary-found', why=' (emutest.zip, "Tap to import")')
        at(n, 120, 168)                                        # import it
        settle(n, 4, 60)
        at(n, 240, 168)                                        # Lookup method
        at(n, 140, 412)                                        # Long press
        check(n, tmp_path, 'dictionary-settings', why=' (Long press, emutest)')

        at(n, 38, 80)                                          # Reading options
        at(n, 38, 80)                                          # Settings
        at(n, 446, 80)                                         # ≡
        at(n, 330, 236)                                        # Bookshelf (All files' rows move with the card)
        at(n, 80, 80)                                          # Recent ⌄
        at(n, 355, 145)                                        # All books
        at(n, 85, 220, quiet=2)                                # the test book's cover
        x4emu(n, 'press', 'right', '--quiet', 1, '--wait', 20)
        settle(n, 1.5, 60)
        x4emu(n, 'tap', *P(70, 163), '--ms', 2500, '--quiet', 1, '--wait', 30)
        settle(n, 2, 60)
        check(n, tmp_path, 'dictionary-lookup', READER_COLS, ' ("quick" boxed, its definition in a pop-up)')
