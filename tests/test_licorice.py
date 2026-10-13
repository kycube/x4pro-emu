"""XTOS Licorice, the stock firmware's new generation (on the device since 2026-10-13: "XTOS Licorice
261013", esp_app_desc `dragon_example` `e77bdf08-dirty`, ESP-IDF v6.1; docs/stock-licorice.md).

The redacted dump copy boots to its Bookshelf in the first refresh. Licorice is the first firmware to
send the UC8279 PSR 3B (UD 1: the gate scan reversed, SHL 0: the sources mirrored; CrossPoint and stock
7.x send 37) and streams its frame for that scan, so before models/epd_core.c honoured the two bits
on the UC8279 its screens came out upside down and 120 rows short. The ≡ tap opens the side drawer
(Desktop, Bookshelf, All files, Cloud, USB mode, Settings): touch reaches it through the GT911 model
unchanged. Skips without the dump (stockkit.stock_flash redacts the copy, NVS and the /radio store).
"""
import json

from stockkit import DUMP_LICORICE, SKIP_IDLE, booted, golden_check, settle, stock_card, tap, x4emu  # noqa: F401

HAMBURGER = (94, 33)                 # portrait (446, 94): the ≡ at the right of the Bookshelf tabs
DRAWER_COLS = (60, 720)              # the drawer draws the date at portrait y 730..760


def test_licorice_boots_upright_and_opens_the_drawer(request, tmp_path, stock_card):
    with booted(request, tmp_path, stock_card, dump=DUMP_LICORICE, icount=SKIP_IDLE) as s:
        x4emu(s.name, 'wait-refresh', '--total', 1, '--timeout', 90)
        settle(s.name, 2, 60)
        psr = [json.loads(ln)['data'] for ln in open(tmp_path / 'epd.jsonl') if '"cmd":"0x00"' in ln]
        assert psr and psr[0].split()[0] == '3b', f'Licorice sent PSR {psr} (3b expected: UD 1, SHL 0)'
        home = tmp_path / 'licorice-home.png'
        x4emu(s.name, 'screenshot', home)
        golden_check(home, 'licorice-home.png', why=' (Bookshelf, "Open a book to start")')
        tap(s.name, *HAMBURGER)
        settle(s.name, 1.5, 60)
        drawer = tmp_path / 'licorice-drawer.png'
        x4emu(s.name, 'screenshot', drawer)
        golden_check(drawer, 'licorice-drawer.png', cols=DRAWER_COLS, why=' (the side drawer)')
