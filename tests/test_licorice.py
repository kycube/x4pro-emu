"""XTOS Licorice, the stock firmware's new generation (on the device since 2026-10-13: "XTOS Licorice
261013", esp_app_desc `dragon_example` `e77bdf08-dirty`, ESP-IDF v6.1; docs/stock-licorice.md).

The redacted dump copy boots to its Bookshelf in the first refresh. Licorice is the first firmware to
send the UC8279 PSR 3B (UD 1: the gate scan reversed, SHL 0: the sources mirrored; CrossPoint and stock
7.x send 37) and streams its frame for that scan, so before models/epd_core.c honoured the two bits
on the UC8279 its screens came out upside down and 120 rows short. The ≡ tap opens the side drawer
(Desktop, Bookshelf, All files, Cloud, USB mode, Settings): touch reaches it through the GT911 model
unchanged. Skips without the dump (stockkit.stock_flash redacts the copy, NVS and the /radio store).
"""
import datetime
import json
import os
import time

from stockkit import (SKIP_IDLE, WHOLE, booted, golden_check, licorice, settle, state, stock_card,  # noqa: F401
                      tap, wait_for, x4emu)

HAMBURGER = (94, 33)                 # portrait (446, 94): the ≡ at the right of the Bookshelf tabs
DRAWER_COLS = (60, 720)              # the drawer draws the date at portrait y 730..760


def first_rtc_read(trace):
    """The BM8563 time (seconds since 1970, as written in its registers) of the firmware's first read of
    registers 02..08 in an I2C trace."""
    for line in open(trace):
        ev = json.loads(line)
        if ev['addr'] == '0x51' and ev['rw'] == 'r' and len(ev['r'].split()) == 7:
            sec, mi, hr, day, _, mon, yr = (int(b, 16) for b in ev['r'].split())
            bcd = lambda v: (v >> 4) * 10 + (v & 15)                       # noqa: E731
            t = datetime.datetime(2000 + bcd(yr), bcd(mon & 0x1f), bcd(day & 0x3f), bcd(hr & 0x3f),
                                  bcd(mi & 0x7f), bcd(sec & 0x7f), tzinfo=datetime.timezone.utc)
            return t.timestamp()
    raise AssertionError('the firmware never read the RTC')


def test_licorice_boots_upright_and_opens_the_drawer(request, tmp_path, stock_card, licorice):
    """Home in refresh 1 with PSR 3B, and the drawer. The RTC chip holds what Licorice keeps there, the
    device's local time (UTC + NVS fw/tz_min, here UTC-5): Licorice shows the chip as it is, so a chip at UTC
    put the emulator's clock five hours ahead of the reader's (nvsedit.rtc_local_offset)."""
    import nvsedit
    started = time.time()
    with booted(request, tmp_path, stock_card, dump=licorice.dump, icount=SKIP_IDLE, trace_i2c=True) as s:
        x4emu(s.name, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(s.name, 2, 60)
        with open(licorice.dump, 'rb') as f:
            offset = nvsedit.rtc_local_offset(f.read(0x10000))
        assert offset == -5 * 3600, f'the dump keeps UTC{offset / 3600:+g}, UTC-5 expected'
        chip = first_rtc_read(tmp_path / 'i2c.jsonl')
        assert abs(chip - (started + offset)) < 300, \
            f'the RTC chip read {datetime.datetime.fromtimestamp(chip, datetime.timezone.utc):%H:%M} (UTC-5 expected)'
        psr = [json.loads(ln)['data'] for ln in open(tmp_path / 'epd.jsonl') if '"cmd":"0x00"' in ln]
        assert psr and psr[0].split()[0] == '3b', f'Licorice sent PSR {psr} (3b expected: UD 1, SHL 0)'
        home = tmp_path / 'licorice-home.png'
        x4emu(s.name, 'screenshot', home)
        golden_check(home, licorice.golden('licorice-home.png'), bands=licorice.home_bands,
                     why=' (Bookshelf, "Open a book to start")')
        tap(s.name, *HAMBURGER)
        settle(s.name, 1.5, 60)
        drawer = tmp_path / 'licorice-drawer.png'
        x4emu(s.name, 'screenshot', drawer)
        golden_check(drawer, licorice.golden('licorice-drawer.png'), cols=DRAWER_COLS, bands=licorice.home_bands,
                     why=' (the side drawer)')


def P(px, py):
    """A portrait point of Licorice's UI as the landscape panel pixel x4emu takes (stockkit's docstring)."""
    return py, 479 - px


READER_COLS = (0, 760)               # the reader has no status bar; its footer clock is at portrait y 767..792


def shot(name, tmp_path, label):
    png = tmp_path / f'licorice-{label}.png'
    x4emu(name, 'screenshot', png)
    return png


def test_licorice_walk(request, tmp_path, stock_card, licorice):
    """The walk a reader takes: All files, the test book opened, a page forward with the right button and
    back with the left, the light panel over the page with the frontlight driven from its slider, the
    Home pad back to the Bookshelf, Settings, and a deep sleep (its "Get Started" screen) with a wake
    that resumes the book (261013) or shows Recent (26102901), not Settings. Each screen against its
    build's golden; the clock regions masked."""
    with booted(request, tmp_path, stock_card, dump=licorice.dump, icount=SKIP_IDLE) as s:
        n = s.name
        x4emu(n, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(n, 2, 60)
        tap(n, *HAMBURGER)
        tap(n, *licorice.drawer('All files'))
        settle(n, 1.5, 60)
        golden_check(shot(n, tmp_path, 'files'), licorice.golden('licorice-files.png'), why=' (All files at the card root)')
        tap(n, *P(150, 245))                                   # Test Book.epub (the second row)
        settle(n, 2, 60)
        page1 = shot(n, tmp_path, 'page1')
        golden_check(page1, licorice.golden('licorice-reader.png'), cols=READER_COLS, why=' (the test book, page 1)')
        x4emu(n, 'press', 'right', '--quiet', 1, '--wait', 20)
        settle(n, 1.5, 60)
        golden_check(shot(n, tmp_path, 'page2'), licorice.golden('licorice-reader-page2.png'), cols=READER_COLS)
        x4emu(n, 'press', 'left', '--quiet', 1, '--wait', 20)
        settle(n, 1.5, 60)
        golden_check(shot(n, tmp_path, 'page1-again'), licorice.golden('licorice-reader.png'), cols=READER_COLS,
                     why=' (left goes back to page 1)')

        # the light panel: pulled from the top edge; the brightness slider's right part drives both
        # LEDC channels (GPIO 8 cool, 9 warm), split by the colour temperature (Neutral: about equal)
        (x1, y1), (x2, y2) = P(240, 5), P(240, 300)
        x4emu(n, 'swipe', x1, y1, x2, y2, '--ms', 600, '--quiet', 1, '--wait', 20)
        settle(n, 1.5, 60)
        golden_check(shot(n, tmp_path, 'light'), licorice.golden('licorice-light.png'), cols=(60, 760),
                     why=' (the light panel; the dimmed page keeps its footer clock)')
        # off at boot: 261013 sets the two LEDC channels up at duty 0; 26102901 leaves LEDC untouched
        # until the light is first used (no channel on GPIO 8/9: null)
        assert json.loads(x4emu(n, 'light').stdout) in ({'cool': 0, 'warm': 0}, {'cool': None, 'warm': None}), \
            'the light is off at boot'
        tap(n, *P(385, 130))
        settle(n, 1.5, 60)
        light = json.loads(x4emu(n, 'light').stdout)
        assert light['cool'] > 50 and light['warm'] > 50, f'the slider did not drive the frontlight: {light}'
        tap(n, *P(240, 600))                                   # the dimmed page below closes the panel
        settle(n, 1.5, 60)

        x4emu(n, 'home', '--quiet', 1, '--wait', 20)           # the Home pad: back to the Bookshelf
        settle(n, 1.5, 60)
        tap(n, *HAMBURGER)
        tap(n, *licorice.drawer('Settings'))
        settle(n, 1.5, 60)
        golden_check(shot(n, tmp_path, 'settings'), licorice.golden('licorice-settings.png'), why=' (Settings)')

        # deep sleep: a held power button; the model pauses the VM with EXT1 armed on GPIO3
        x4emu(n, 'hold', 'power', '--ms', 1500)
        st = wait_for(lambda: (v := state(n))['sleep']['sleeping'] and v, 30, 'Licorice to deep-sleep', every=0.2)
        assert st['sleep']['deep'] and st['sleep']['ext1_sel'] == '0x8', st['sleep']
        golden_check(shot(n, tmp_path, 'sleep'), licorice.golden('licorice-sleep.png'), cols=WHOLE, why=' (the sleep screen)')
        refreshes = st['refresh_count']
        x4emu(n, 'press', 'power')                             # extended to a 1.5 s hold while asleep
        st = wait_for(lambda: (v := state(n))['refresh_count'] > refreshes and not v['sleep']['sleeping'] and v,
                      60, 'Licorice to wake and paint')
        assert st['sleep']['wakes'] == 1 and st['sleep']['wakeup_cause'] == '0x2', st['sleep']
        settle(n, 2, 60)
        log = x4emu(n, 'log').stdout
        assert 'rst:0x5 (DSLEEP)' in log, log[-600:]
        if licorice.tag == 'v261013':
            golden_check(shot(n, tmp_path, 'woken'), licorice.golden('licorice-reader.png'), cols=READER_COLS,
                         why=' (a wake resumes the last book, page 1, not the screen it slept on)')
        else:   # 26102901 wakes to Home's Recent tab, 26110701 to its Desktop, the book on top (docs/stock-licorice.md)
            golden_check(shot(n, tmp_path, 'woken'), licorice.golden('licorice-woken.png'), bands=licorice.home_bands,
                         why=' (a wake shows Home with the book just read: Recent, or the Desktop on 26110701)')
