"""tools/stockstrings.py: reading and relabelling the stock app's UI string packs.

The fast tests work on a scratch copy of images/device/stock-app0-7.2.4.bin (gitignored, so they skip
without it) and check the three packs the tool handles -- `labels` (234 groups, every menu/settings
label), `counts` (18) and the headerless `toasts` pack (688) -- plus the two ways `set` writes:

* **in place** when the text is no longer than the old string and no other slot shares it: the bytes
  are overwritten and NUL-padded, the offset table does not move, and nothing else in the image
  changes except the checksum byte and the appended SHA-256;
* **appended** otherwise: the text goes into the pack's free tail and the group's u16 offset is
  repointed. The stock image has **no** free tail (docs/stock-firmware.md §6 expects ~1 KB after the
  label blob; the bitmap directory starts 2 bytes after it, at 0x3c3fb988), so a longer label is
  refused until `compact` -- a suffix-merging repack of the blob, same size, same strings -- frees
  497 bytes of it. Both paths are re-parsed here and handed to esptool as a second opinion.

`test_stock_relabelled_menu` is the emulator half: it boots a scratch copy of the device dump whose
label pack says "Transfer" instead of "USB Mode" (the in-place path, shorter text) and asserts that
the nav menu differs from tests/golden/stock-menu.png only inside the third entry's box -- the ink of
"USB Mode" sits at landscape x 319..341 in the golden, with nothing else between x 260 (the end of
"All Files") and x 401 (the start of "Cloud Sync"), so every changed pixel must fall in x 290..370.
It reuses test_stock.py's `stock_card`, `masked_diff`, `tap_repaints`, `state` and `wait_for`.
"""
import os, shutil, subprocess, sys, pytest
from conftest import x4emu, ROOT, PY, QEMU
from test_stock import (DUMP, STATUS_BAR_COLS, PANEL_W, masked_diff, state, wait_for,   # noqa: F401
                        tap_repaints, stock_card)                        # noqa: F401 (fixture)

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import stockstrings                                                      # noqa: E402
import stockdev                                                          # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'stockstrings.py')
APP = os.path.join(ROOT, 'images', 'device', 'stock-app0-7.2.4.bin')
NVSEDIT = os.path.join(ROOT, 'tools', 'nvsedit.py')
MENU_GOLDEN = os.path.join(ROOT, 'tests', 'golden', 'stock-menu.png')
USB_ENTRY_COLS = (290, 370)      # the third nav-menu entry's box; its ink is x 319..341
MENU_ROWS = 265                  # the menu panel ends where the dithered home screen starts


def run(image, *args, rc=0):
    r = subprocess.run([PY, TOOL, str(image), *map(str, args)], capture_output=True, text=True)
    assert r.returncode == rc, f'stockstrings {args} exited {r.returncode} (wanted {rc}):\n{r.stdout}\n{r.stderr}'
    return r.stdout if rc == 0 else r.stderr


def packs(image, name='labels'):
    data = bytearray(open(image, 'rb').read())
    return stockstrings.parse_pack(data, stockdev.app_base(data), name)


def esptool_ok(image):
    """esptool's own opinion of the image: (checksum valid, hash valid). Bare app images only."""
    r = subprocess.run([PY, '-m', 'esptool', '--chip', 'esp32s3', 'image-info', str(image)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    return ('Checksum:' in out and '(valid)' in out.split('Checksum:')[1].split('\n')[0],
            'Validation hash:' in out and '(valid)' in out.split('Validation hash:')[1].split('\n')[0])


@pytest.fixture
def app(tmp_path):
    """A scratch copy of the bare stock app image (images/ is never written)."""
    if not os.path.exists(APP):
        pytest.skip('images/device/stock-app0-7.2.4.bin not available')
    dst = tmp_path / 'app.bin'
    shutil.copyfile(APP, dst)
    return dst


def test_list_reads_all_three_packs(app):
    """`list` prints the 234 label groups with the ids docs/stock-firmware.md §6 names, and the two
    other packs parse as well: 18 count formats and the 688-group headerless toast pack."""
    out = run(app, 'list')
    assert '234 groups x 4 languages (zh-CN, en, zh-TW, ja)' in out and 'blob 13528 bytes' in out
    assert len([l for l in out.splitlines() if l.startswith('  ')]) == 234
    for group, en in ((0, 'Bookshelf'), (10, 'All Files'), (11, 'USB Mode'), (13, 'Settings'),
                      (70, 'Screen Capture'), (91, 'Language'), (169, 'About Device')):
        assert f'en: {en} ' in out or f'en: {en}\n' in out, f'group {group} en = {en} missing'
    # the four languages of one group, and --lang / --grep narrowing
    assert 'zh-CN: USB 模式 | en: USB Mode | zh-TW: USB 模式 | ja: USB モード' in out
    en = run(app, 'list', '--lang', 'en')
    assert '  11  USB Mode' in en and 'zh-CN' not in en.split('\n', 2)[2]
    grep = run(app, 'list', '--lang', 'ja', '--grep', 'Screen Capture')
    assert [l.split()[0] for l in grep.splitlines() if l.startswith('  ')] == ['70']
    assert 'スクリーンショット' in grep
    # get, by name and by index
    assert run(app, 'get', '13', '--lang', 'en').strip().startswith('13  Settings')
    assert run(app, 'get', '13', '--lang', '1') == run(app, 'get', '13', '--lang', 'en')
    assert '設定' in run(app, 'get', '13', '--lang', 'ja')
    # the other two packs
    counts = run(app, 'list', '--pack', 'counts')
    assert '18 groups x 4 languages' in counts and 'en: %d books' in counts
    toasts = run(app, 'list', '--pack', 'toasts', '--lang', 'en')
    assert '688 groups x 4 languages' in toasts and 'blob 63373 bytes' in toasts
    assert '   0  Cancel' in toasts and 'Region set successfully' in toasts
    # every pack of the stock image is full: the free tail is 2 / 1 / 0 bytes
    assert [packs(app, n).free_end - packs(app, n).free_start for n in ('labels', 'counts', 'toasts')] \
        == [2, 1, 0]


def test_set_in_place_overwrites_and_nul_pads(app):
    """A shorter label is written over the old one: same offset, the rest of the slot NUL-padded, no
    other byte of the image touched except the checksum and the hash."""
    before = bytearray(open(app, 'rb').read())
    p0 = packs(app)
    off = p0.offsets[11 * 4 + 1]
    out = run(app, 'set', '11', 'en', 'Transfer')
    assert "'USB Mode' -> 'Transfer' (in-place" in out and 'checksum' in out and 'INVALID' not in out

    p = packs(app)
    assert p.group_strings(11) == ['USB 模式', 'Transfer', 'USB 模式', 'USB モード']
    assert p.offsets == p0.offsets, 'an in-place write must not move any offset'
    assert bytes(p.blob[off:off + 9]) == b'Transfer\0'
    after = bytearray(open(app, 'rb').read())
    lay = stockdev.image_layout(after, 0)
    changed = {i for i in range(len(after)) if after[i] != before[i]}
    slot_bytes = set(range(p.blob_off + off, p.blob_off + off + 9))       # 8 chars + the NUL
    assert changed <= slot_bytes | {lay['checksum_off']} | \
        set(range(lay['hash_off'], lay['hash_off'] + 32)), sorted(changed - slot_bytes)[:20]
    assert len(changed & slot_bytes) == 8, 'the eight bytes of "USB Mode" should have changed'
    assert stockdev.verify_image(after, 0) == (True, True)
    assert esptool_ok(app) == (True, True)
    # a shorter text NUL-pads the rest of the old slot instead of leaving 'r' behind
    run(app, 'set', '11', 'en', 'Files')
    assert bytes(packs(app).blob[off:off + 9]) == b'Files\0\0\0\0'
    assert packs(app).group_strings(11)[1] == 'Files'


def test_set_longer_needs_compact_and_then_appends(app):
    """A longer label does not fit the stock pack (2 bytes of tail); `compact` frees 497 by merging
    every string that is a suffix of another, and the label is then appended and repointed."""
    err = run(app, 'set', '11', 'en', 'File Transfer', rc=2)
    assert "free tail is 2 byte(s) and 14 are needed" in err and 'compact' in err
    assert packs(app).group_strings(11)[1] == 'USB Mode', 'a refused set must not touch the image'

    strings0 = [packs(app).string(o) for o in packs(app).offsets]
    out = run(app, 'compact')
    assert 'free tail 2 -> 499 (+497)' in out
    p = packs(app)
    assert [p.string(o) for o in p.offsets] == strings0, 'compaction changed a string'
    assert p.blob_size == 13528, 'compaction must keep the blob (and the region) the same size'
    assert esptool_ok(app) == (True, True)

    was = list(packs(app).offsets)
    out = run(app, 'set', '11', 'en', 'File Transfer')
    assert "'USB Mode' -> 'File Transfer' (appended" in out
    p = packs(app)
    slot = 11 * 4 + 1
    assert p.group_strings(11) == ['USB 模式', 'File Transfer', 'USB 模式', 'USB モード']
    assert p.offsets[slot] == p.free_start - 14 and p.offsets[slot] < 0x10000
    assert p.offsets[:slot] == was[:slot] and p.offsets[slot + 1:] == was[slot + 1:]  # only that slot
    assert p.free_end - p.free_start == 499 - 14
    assert stockdev.verify_image(bytearray(open(app, 'rb').read()), 0) == (True, True)
    assert esptool_ok(app) == (True, True)

    # filling the tail: the last append crosses the old blob end and grows blob_size into the two
    # bytes of padding after it, and the one after that is refused
    free = p.free_end - p.free_start
    run(app, 'set', '12', 'en', 'x' * (free - 4))
    assert packs(app).free_end - packs(app).free_start == 3
    out = run(app, 'set', '9', 'en', 'ab', '--append')      # --append: it would fit in place
    assert 'appended' in out and packs(app).blob_size > 13528, 'blob_size did not grow into the pad'
    assert packs(app).group_strings(9)[1] == 'ab' and packs(app).free_end == packs(app).free_start
    assert esptool_ok(app) == (True, True)
    err = run(app, 'set', '9', 'en', 'abc', rc=2)
    assert 'free tail is 0 byte(s)' in err


def test_shared_strings_are_not_dragged_along(app):
    """234 groups x 4 slots share 752 offsets: 'USB 模式' is one string for zh-CN and zh-TW. An
    in-place overwrite would change both, so `set` appends instead (and says so), while `--shared`
    asks for the old behaviour on purpose."""
    p = packs(app)
    assert p.offsets[11 * 4 + 0] == p.offsets[11 * 4 + 2], 'zh-CN and zh-TW should share the string'
    err = run(app, 'set', '11', 'zh-CN', '模式', rc=2)
    assert 'shared with 11/zh-TW' in err and '--shared' in err

    run(app, 'compact')
    out = run(app, 'set', '11', 'zh-CN', '模式')
    assert 'appended' in out and 'they keep it' in out
    assert packs(app).group_strings(11)[:3] == ['模式', 'USB Mode', 'USB 模式']

    out = run(app, 'set', '11', 'zh-TW', 'X模式', '--shared')     # nothing shares it any more
    assert 'in-place' in out
    assert packs(app).group_strings(11)[2] == 'X模式'


def test_restore_puts_the_pack_region_back(app, tmp_path):
    """`restore --from ORIGINAL` copies header + offsets + blob + padding back, byte for byte."""
    run(app, 'compact')
    run(app, 'set', '11', 'en', 'File Transfer')
    run(app, 'set', '70', 'en', 'Grab', '--append')
    assert open(app, 'rb').read() != open(APP, 'rb').read()
    out = run(app, 'restore', '--from', APP)
    assert 'labels pack restored' in out and 'changed' in out
    assert open(app, 'rb').read() == open(APP, 'rb').read(), 'restore is not byte-exact'
    assert run(app, 'restore', '--from', APP).count('was already identical') == 3   # idempotent


def test_refusals(app, tmp_path):
    """Bad arguments and foreign images are refused with exit 2, not a corrupted pack."""
    assert 'out of range' in run(app, 'get', '999', rc=2)
    assert 'unknown language' in run(app, 'set', '11', 'de', 'x', rc=2)
    data = bytearray(open(app, 'rb').read())          # argv cannot carry a NUL: call the module
    with pytest.raises(stockstrings.StockStringsError, match='cannot contain a NUL'):
        stockstrings.set_string(data, 0, 'labels', 11, 'en', 'a\0b')
    junk = tmp_path / 'junk.bin'
    junk.write_bytes(b'\x00' * 4096)
    assert 'no ESP image magic' in run(junk, 'list', rc=2)
    # a stock image whose pack header was scribbled on is refused rather than parsed
    bad = tmp_path / 'bad.bin'
    data = bytearray(open(APP, 'rb').read())
    p = packs(app)
    data[p.header_off:p.header_off + 2] = (233).to_bytes(2, 'little')
    bad.write_bytes(data)
    assert 'not stock xteink_app 7.2.4' in run(bad, 'list', rc=2)


def test_works_on_a_16mb_flash_image(tmp_path):
    """The same edits through app0 at 0x10000 (`stockdev.app_base`), on a copy of the device dump."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    assert 'USB Mode' in run(flash, 'get', '11', '--lang', 'en')
    assert '16 MB flash image, app0 at 0x10000' in run(flash, 'set', '11', 'en', 'Transfer')
    assert packs(flash).group_strings(11)[1] == 'Transfer'
    assert packs(flash).base == 0x10000
    data = bytearray(open(flash, 'rb').read())
    assert stockdev.verify_image(data, 0x10000) == (True, True)
    assert len(data) == 0x1000000, 'a flash image must keep its size'
    run(flash, 'restore', '--from', DUMP)
    assert open(flash, 'rb').read() == open(DUMP, 'rb').read()


# ---------------------------------------------------------------- the emulator half
@pytest.fixture
def relabelled(tmp_path, request, stock_card):                                  # noqa: F811
    """test_stock.py's `stock` fixture with one more step: the label pack of the scratch flash copy
    says "Transfer" where the stock says "USB Mode" (group 11, en) before the boot."""
    if not os.path.exists(DUMP):
        pytest.skip('device dump not available')
    if not os.path.exists(QEMU):
        pytest.skip('qemu not built')
    if not os.path.exists(MENU_GOLDEN):
        pytest.skip('tests/golden/stock-menu.png not available')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP, flash)
    subprocess.run([PY, NVSEDIT, str(flash), 'set-u8', 'user_config', 'net_en', '0'],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run([PY, TOOL, str(flash), 'set', '11', 'en', 'Transfer'],
                   check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    name = 'st-' + request.node.name.replace('[', '-').replace(']', '').replace('=', '-')
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd), '--boot-hold-power')
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def diff_bbox(a_png, b_png, cols=(STATUS_BAR_COLS, PANEL_W - 60)):
    """(x0, y0, x1, y1) of the pixels that differ between two panel screenshots inside `cols`, or
    None. Columns 0..59 are the portrait status bar and the last 60 hold the nav menu's date, both
    masked the way `golden_check` masks them."""
    from PIL import Image, ImageChops
    a = Image.open(a_png).convert('1')
    b = Image.open(b_png).convert('1')
    box = (cols[0], 0, min(cols[1], a.width), a.height)
    d = ImageChops.difference(a.crop(box), b.crop(box))
    bb = d.getbbox()
    return None if bb is None else (bb[0] + cols[0], bb[1], bb[2] - 1 + cols[0], bb[3] - 1)


def test_stock_relabelled_menu(relabelled):
    """The relabelled image boots and its nav menu reads "Transfer": every pixel that differs from
    tests/golden/stock-menu.png is inside the third entry's box (x 290..370) and inside the menu
    panel (y < 265), so no other label, icon or row moved -- the data-only relabel of
    docs/stock-firmware.md §10.4 works on the device's own firmware."""
    name, tmp = relabelled
    x4emu(name, 'wait-text', 'main_task: Returned from app_main()', '--timeout', '120')
    st = wait_for(lambda: (s := state(name))['refresh_count'] >= 2 and s, 120, 'home paint')
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    assert st['epd_unknown_cmds'] == 0 and st['spi2']['dma_errors'] == 0, st['spi2']

    tap_repaints(name, 88, 38)                     # the hamburger opens the nav menu
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    shot = tmp / 'menu.png'
    x4emu(name, 'screenshot', str(shot))

    bb = diff_bbox(shot, MENU_GOLDEN)
    assert bb is not None, 'the menu is pixel-identical to the golden: the relabel did not show'
    x0, y0, x1, y1 = bb
    assert USB_ENTRY_COLS[0] <= x0 and x1 <= USB_ENTRY_COLS[1], \
        f'pixels outside the USB Mode entry changed: bounding box {bb}'
    assert y1 < MENU_ROWS, f'the change reaches out of the menu panel: bounding box {bb}'
    # and the change is a real relabel, not a stray pixel
    assert masked_diff(shot, MENU_GOLDEN, (USB_ENTRY_COLS[0], USB_ENTRY_COLS[1])) > 100, \
        'too few pixels changed for a redrawn label'
    assert masked_diff(shot, MENU_GOLDEN, (STATUS_BAR_COLS, USB_ENTRY_COLS[0])) == 0
    assert masked_diff(shot, MENU_GOLDEN, (USB_ENTRY_COLS[1], PANEL_W - 60)) == 0
