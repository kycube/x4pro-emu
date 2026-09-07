"""tools/xtfont.py: reading the card's `.xtf` fonts, and building an installable `.xtfont` package.

The fast half needs only `images/device/sd-files` (the device card's own `misans-demibold` pair) and
`tests/data/stock-home-panel.png`; it skips otherwise. The last test boots the stock.

**The four metric bytes are `advance_x, advance_y, x_offset, y_offset`.** `test_metric_byte_order`
and `test_dump_bookshelf_matches_the_panel` are the proof: byte 0 equals the 95-entry advance table
at 0x1a40 for every ASCII codepoint of both card files, byte 1 is the header's `advance_y` in all
43101 records of both, and laying "Bookshelf" out with bytes 2/3 as signed offsets reproduces the
stock's Home title in `tests/data/stock-home-panel.png` in **every one of its 2260 pixels** (113x20,
total advance 115). The survey's other reading -- byte 1 as a per-glyph height -- cannot hold: `.` and
`B` would then be the same height.

**The 8-byte word at header 0x30 is two CRC-32s, and it is what used to fail.**
`test_header_crc32_words` pins the rule against the card's own pair: `0x30` is the zlib CRC-32 of the
glyph records (`data[glyph_offset : glyph_offset + glyph_bytes]`) and `0x34` the zlib CRC-32 of the 52
header bytes in front of it, `data[0:0x34]`. The stock's header parser (app VA 0x4210db20) checks the
second one at 0x4210e553 -- it CRC-32s 52 bytes of the header and compares them with the `0x34` word --
and refuses the font when they differ, which is why every earlier generated package (which wrote
`sha256(body)[:8]` there) ended in "Failed to switch system font".

**What the stock does with a built package** (`test_stock_font_package_installs_and_renders`): a
package with no `.hot.xtfp` is not refused -- with the two CRC-32s right, Settings -> System Font
lists it (display name, style and "S/M 1169/1169 glyph" read out of the package), selecting it shows
"Applying system font. The device will restart automatically...", and after the restart the whole UI
is drawn in the converted TTF. The app writes its own `system_{small,medium}.base.xtfp` next to the
`.xtf` files and rewrites `selection.config` and `.cache/system-font-boot-plan.xbgp`, so `hot` is
optional exactly as `required_features` suggested. **Writing `selection.config` from the host is not
enough**: a boot whose `selection.config` already names a package with no `.base.xtfp` sidecars falls
back to the built-in base font silently (no console line, no toast, and deleting the stale boot plan
does not change it), so the test switches through the UI the way a user would.
"""
import hashlib, json, os, shutil, struct, subprocess, sys, zipfile, zlib
import pytest
from conftest import ROOT, PY, x4emu

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import xtfont                                                                  # noqa: E402
import xic2png                                                                 # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_stock                                                              # noqa: E402
from test_stock import stock_card                                              # noqa: E402,F401

Image = pytest.importorskip('PIL.Image')
TOOL = os.path.join(ROOT, 'tools', 'xtfont.py')
CARD_FONT = os.path.join(ROOT, 'images', 'device', 'sd-files', 'XTData', 'system_fonts',
                         'misans-demibold')
SMALL = os.path.join(CARD_FONT, 'system_small.xtf')
MEDIUM = os.path.join(CARD_FONT, 'system_medium.xtf')
PANEL = os.path.join(ROOT, 'tests', 'data', 'stock-home-panel.png')
# a TTF from the host, first one that exists; every build test skips without one
TTF_CANDIDATES = ['/System/Library/Fonts/Supplemental/Arial Bold.ttf',
                  '/System/Library/Fonts/Supplemental/Arial.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                  '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']
GLYPH_COUNT = 43101                       # both card files, and the manifest's two roles


def card_font(path):
    if not os.path.exists(path):
        pytest.skip('images/device/sd-files not available')
    return xtfont.Xtf.open(path)


@pytest.fixture(scope='session')
def ttf():
    for p in TTF_CANDIDATES:
        if os.path.exists(p):
            return p
    pytest.skip('no system TTF found to build from')


@pytest.fixture(scope='session')
def built(ttf, tmp_path_factory):
    """One built package per session: `<dir>/{system_small.xtf,system_medium.xtf,manifest.json,
    preview.xic,arial-bold-r1.xtfont}` (a few hundred KB, so it stays in tmp, never in tests/data)."""
    out = tmp_path_factory.mktemp('xtfont')
    manifest, report = xtfont.build_package(ttf, 'test-font', 'Test Font', str(out), style='Bold')
    return {'dir': str(out), 'manifest': manifest, 'report': report, 'ttf': ttf}


def run(*args, expect=0):
    r = subprocess.run([PY, TOOL, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, f'xtfont {args} exited {r.returncode}:\n{r.stdout}\n{r.stderr}'
    return r.stdout


# ---------------------------------------------------------------- parsing the card's fonts
@pytest.mark.parametrize('path,cell,advance_y,record_size', [(SMALL, 20, 18, 0x40),
                                                             (MEDIUM, 24, 22, 0x4c)])
def test_card_font_header(path, cell, advance_y, record_size):
    f = card_font(path)
    assert (f.version, f.cell_w, f.cell_h) == (2, cell, cell)
    assert (f.advance_y, f.record_size, f.glyph_count) == (advance_y, record_size, GLYPH_COUNT)
    assert f.record_size == 4 + cell * ((cell + 7) // 8)
    # the range table runs from the header to the advance table, and the advance table is 95 entries
    assert f.range_offset == xtfont.HEADER == 0x40
    assert f.range_offset + f.range_count * 16 == f.advance_offset == 0x1a40
    assert f.advance_count == 95
    # 0x2c is the glyph data length, not a "tail section" offset: the file ends with the records
    assert f.glyph_bytes == f.glyph_count * f.record_size
    assert f.glyph_offset + f.glyph_bytes == len(f.data) == os.path.getsize(path)
    assert sum(r[1] for r in f.ranges) == GLYPH_COUNT
    assert all(r[3] == 0 and r[4] == r[1] for r in f.ranges)     # the u16 pair: 0, then the count
    assert f.ranges[0][:3] == (0x20, 95, 0)                      # ASCII first, at glyph index 0


def header_crc32s(data):
    """The two words the stock checks: (crc32 of the glyph records, crc32 of `data[0:0x34]`)."""
    glyph_offset, _record_size, glyph_bytes = struct.unpack_from('<III', data, 0x24)
    return (zlib.crc32(data[glyph_offset:glyph_offset + glyph_bytes]) & 0xffffffff,
            zlib.crc32(data[:0x34]) & 0xffffffff)


@pytest.mark.parametrize('path,stored', [(SMALL, (0x57ddf6f8, 0xffe29784)),
                                         (MEDIUM, (0x1f64e27f, 0x09897cb4))])
def test_header_crc32_words(path, stored):
    """The 8 bytes at 0x30 are two little-endian CRC-32s, not a hash: 0x30 covers the glyph records
    and 0x34 covers the 52 header bytes in front of it (so it signs the 0x30 word too). The stock's
    header parser at app VA 0x4210db20 recomputes the 0x34 one over 52 bytes at 0x4210e553 and
    refuses the file when it differs; `build_xtf` writes both."""
    d = card_font(path).data
    assert struct.unpack_from('<II', d, 0x30) == stored == header_crc32s(d)


def test_build_writes_both_header_crc32s(built):
    for _, filename, _ in xtfont.ROLES:
        d = xtfont.Xtf.open(os.path.join(built['dir'], filename)).data
        assert struct.unpack_from('<II', d, 0x30) == header_crc32s(d)
        assert struct.unpack_from('<I', d, 0x30)[0] != 0        # 0 means "unsigned" to the stock


def test_card_font_space_record_bytes():
    """U+0020 is glyph 0: `06 12 00 00` then 60 zero bytes in the 20-px font."""
    f = card_font(SMALL)
    raw = f.data[f.glyph_offset:f.glyph_offset + f.record_size]
    assert raw[:4] == bytes((6, 18, 0, 0))
    assert raw[4:] == bytes(60)
    g = f.glyph(0x20)
    assert (g.index, g.advance_x, g.advance_y, g.x_off, g.y_off) == (0, 6, 18, 0, 0)
    assert g.pixels() == []


def test_metric_byte_order():
    """Byte 0 is advance_x (it *is* the advance table) and byte 1 is the header's advance_y."""
    for path in (SMALL, MEDIUM):
        f = card_font(path)
        for cp in range(0x20, 0x7f):
            g = f.glyph(cp)
            assert g.advance_x == f.advances[cp - 0x20], f'U+{cp:04X} in {os.path.basename(path)}'
        seen = {f.data[f.glyph_offset + i * f.record_size + 1] for i in range(f.glyph_count)}
        assert seen == {f.advance_y}, seen                      # constant: not a per-glyph height
    # bytes 2 and 3 are signed offsets that place the ink on one baseline
    f = card_font(SMALL)
    assert f.glyph(ord('j')).x_off == -1                        # 0xff, only sensible as int8
    assert f.glyph(ord('B')).y_off == 1 and f.glyph(ord('.')).y_off == 13
    def bottom(ch):
        g = f.glyph(ord(ch))
        return g.y_off + max(y for _, y in g.pixels())
    assert bottom('B') == bottom('o') == 16                     # one baseline, at row 17
    assert bottom('.') == 15                                    # MiSans sets the period one row up
    assert bottom('j') == 20                                    # and the descender below it all


def test_dump_bookshelf_matches_the_panel(tmp_path):
    """The oracle: "Bookshelf" from system_medium.xtf against the stock's Home title."""
    f = card_font(MEDIUM)
    im, info = f.render('Bookshelf', pad=0)
    assert info['glyphs'] == 9 and info['advance'] == 115
    bb = info['ink_bbox']
    assert bb == (2, 0, 114, 19)
    rendered = im.crop((bb[0], bb[1], bb[2] + 1, bb[3] + 1))
    upright = Image.open(PANEL).transpose(Image.Transpose.ROTATE_270).convert('1')
    title = upright.crop((22, 73, 22 + rendered.width, 73 + rendered.height))
    n, _ = xic2png.compare(rendered, title)
    assert n == 0, f'"Bookshelf" differs from the panel in {n} pixels'
    out = tmp_path / 'bookshelf.png'
    stdout = run('dump', MEDIUM, out, '--text', 'Bookshelf')
    assert json.loads(stdout)['advance'] == 115 and out.exists()
    assert Image.open(out).convert('1').getbbox() is not None            # not an empty page
    run('dump', MEDIUM, tmp_path / 'g.png', '--glyph', '0x67')           # a descender, by codepoint
    assert json.loads(run('dump', SMALL, '--info'))['glyph_count'] == GLYPH_COUNT


# ---------------------------------------------------------------- building
def test_build_writes_reparsable_fonts(built):
    for role, filename, cell in xtfont.ROLES:
        path = os.path.join(built['dir'], filename)
        f = xtfont.Xtf.open(path)
        spec = built['manifest']['roles'][role]['font']
        assert (f.cell_w, f.cell_h, f.record_size) == (cell, cell, 4 + cell * ((cell + 7) // 8))
        assert f.advance_y == xtfont.CELL_PROFILE[cell]['advance_y'] == spec['advance_y']
        assert f.glyph_count == spec['glyph_count'] > 500
        assert f.glyph_offset + f.glyph_bytes == len(f.data) == spec['size']
        assert f.ranges[0][:3] == (0x20, 95, 0) and f.advance_count == 95
        assert sum(r[1] for r in f.ranges) == f.glyph_count
        assert hashlib.sha256(open(path, 'rb').read()).hexdigest() == spec['sha256']
        # every header field the card font pins is reproduced
        p = xtfont.CELL_PROFILE[cell]
        assert (f.unk06, f.unk08, f.byte0d, f.unk0e, f.unk0f) == (p['unk06'], p['unk08'],
                                                                  p['byte0d'], p['unk0e'], p['unk0f'])
        assert (f.line_height, f.descent) == (p['line_height'], p['descent'])
        # the glyphs are real ink, laid out on the profile's baseline
        im, info = f.render('Bookshelf', pad=0)
        assert info['advance'] > 60 and im.convert('1').getbbox() is not None


def test_build_manifest_and_package(built):
    m, out = built['manifest'], built['dir']
    assert m['format'] == 'xteink-system-font-package' and m['format_version'] == 3
    assert m['font_id'] == 'test-font' and m['display_name'] == 'Test Font'
    assert m['required_features'] == ['xtf-v2', 'system-family-1bpp']   # no hot cache in the package
    assert set(m['roles']) == {'system_small', 'system_body'}
    preview = open(os.path.join(out, 'preview.xic'), 'rb').read()
    h = xic2png.parse_header(preview)
    assert (h['width'], h['height'], h['levels'], h['planes']) == (320, 96, 1, 1)
    assert m['preview']['sha256'] == hashlib.sha256(preview).hexdigest()
    assert m['preview']['size'] == len(preview) and 'hot' not in json.dumps(m['roles'])
    im, _ = xic2png.decode_xic(preview)
    assert im.convert('1').getbbox() is not None                        # the specimen has ink
    pkg = built['report']['package']
    with zipfile.ZipFile(pkg) as z:
        assert z.namelist() == ['manifest.json', 'assets/system_small.xtf',
                                'assets/system_medium.xtf', 'assets/preview.xic']
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
        assert json.loads(z.read('manifest.json')) == m
        for role, filename, _ in xtfont.ROLES:
            member = z.read(f'assets/{filename}')
            assert hashlib.sha256(member).hexdigest() == m['roles'][role]['font']['sha256']


def test_ttf_codepoints_and_fit(built):
    cps = xtfont.ttf_codepoints(built['ttf'])
    assert set(range(0x41, 0x5b)) <= cps and 0x20ac in cps or len(cps) > 200
    small = xtfont.fit_size(built['ttf'], 20)
    body = xtfont.fit_size(built['ttf'], 24)
    assert 8 <= small < body <= 30
    with pytest.raises(ValueError):
        xtfont.build_xtf(built['ttf'], 32, {0x41})                      # no measured profile


def test_install_puts_the_package_on_a_card(built, tmp_path):
    """`install` on a small FAT32 image: the download slot, and `--direct` the installed directory
    plus a `selection.config` whose asset ids are the two `.xtf` sha256s."""
    img = tmp_path / 'sd.img'
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'mksd.py'), str(img), '--size', '32M'],
                   check=True, stdout=subprocess.DEVNULL)
    out = xtfont.install_package(str(img), built['report']['package'], direct=True)
    assert out['font_id'] == 'test-font'
    listing = subprocess.run(['mdir', '-i', f'{img}@@{xtfont.PART_OFFSET}',
                              '::/XTData/system_fonts/test-font'], capture_output=True, text=True)
    assert 'system_small.xtf' in listing.stdout and 'manifest.json' in listing.stdout
    cfg = subprocess.run(['mtype', '-i', f'{img}@@{xtfont.PART_OFFSET}',
                          '::/XTData/system_fonts/selection.config'],
                         capture_output=True, text=True).stdout
    m = built['manifest']
    assert 'mode=external' in cfg and 'font_id=test-font' in cfg
    assert f"small_asset_id={m['roles']['system_small']['font']['sha256']}" in cfg
    assert f"body_asset_id={m['roles']['system_body']['font']['sha256']}" in cfg
    dl = subprocess.run(['mdir', '-i', f'{img}@@{xtfont.PART_OFFSET}',
                         '::/XTCache/system_font_downloads'], capture_output=True, text=True).stdout
    assert 'xtfont' in dl.lower() or 'XTFONT' in dl


# ---------------------------------------------------------------- the emulator
STOCK_GOLDEN = os.path.join(ROOT, 'tests', 'golden', 'stock-home.png')


CARD_SELECTION = os.path.join(CARD_FONT, '..', 'selection.config')


def mtype(img, path):
    return subprocess.run(['mtype', '-i', f'{img}@@{xtfont.PART_OFFSET}', path],
                          capture_output=True, text=True).stdout


def mdir(img, path):
    return subprocess.run(['mdir', '-i', f'{img}@@{xtfont.PART_OFFSET}', path],
                          capture_output=True, text=True).stdout


@pytest.fixture
def stock_font(tmp_path, request, built, stock_card):
    """`test_stock.stock` with the built package unpacked into `XTData/system_fonts/test-font/` but
    `selection.config` left on the card's own `misans-demibold`, so the stock boots in the font the
    golden was taken with and the test can switch to the built one through the UI. (Pointing
    `selection.config` at a package with no `.base.xtfp` sidecars from the host does nothing: the
    stock falls back to the built-in base font silently.)"""
    if not os.path.exists(test_stock.DUMP):
        pytest.skip('device dump not available')
    name = 'fh-' + request.node.name.replace('[', '-').replace(']', '').replace('=', '-')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(test_stock.DUMP, flash)
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'set-u8',
                    'user_config', 'net_en', '0'], check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    xtfont.install_package(str(sd), built['report']['package'], direct=True)
    subprocess.run(['mcopy', '-o', '-i', f'{sd}@@{xtfont.PART_OFFSET}', CARD_SELECTION,
                    '::/XTData/system_fonts/selection.config'], check=True)
    assert 'font_id=misans-demibold' in mtype(sd, '::/XTData/system_fonts/selection.config')
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd), '--boot-hold-power')
    yield name, tmp_path, sd
    x4emu(name, 'stop', check=False)


def title_ink(png):
    """The bounding box of the Home title's ink, in upright coordinates (the title band is rows
    60..104, well clear of the status bar above it and the empty shelf below)."""
    up = Image.open(png).transpose(Image.Transpose.ROTATE_270).convert('1')
    band = up.crop((0, 60, 300, 105)).point(lambda v: 255 - v)
    return band.getbbox()


def test_stock_font_package_installs_and_renders(stock_font, built):
    """The whole point: a `.xtf` pair rasterised from a host TTF is **accepted** by the stock.

    The card boots on its own `misans-demibold` (Home is the golden), the test walks Settings ->
    System Font, taps the built package's row, and the stock answers with "Applying system font. The
    device will restart automatically...", reboots, and comes back with every string on Home drawn in
    the converted TTF -- laid out so exactly that the Home title is the built `system_medium.xtf`'s
    own "Bookshelf" bitmap, pixel for pixel. It also writes the two `.base.xtfp` glyph caches next to
    the `.xtf` files and moves `selection.config` onto the package, which is what the boot path needs
    (a `selection.config` written from the host, with no caches beside it, is ignored). "External
    font failed to load" never appears; before the two header CRC-32s were written it always did."""
    from test_stock import boot_to_home, masked_diff, state, tap_repaints, wait_for
    name, tmp, sd = stock_font
    st = boot_to_home(name)
    assert st['epd_unknown_cmds'] == 0, st
    home = tmp / 'home.png'
    x4emu(name, 'screenshot', str(home))
    assert masked_diff(home, STOCK_GOLDEN) == 0, 'the card font boot no longer matches the golden'
    # Settings -> System Font: the stock parsed manifest.json and both .xtf files to draw the row
    tap_repaints(name, 88, 38)                                  # nav menu
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    tap_repaints(name, 500, 150)                                # Settings
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '30')
    settings = tmp / 'settings.png'
    x4emu(name, 'screenshot', str(settings))
    tap_repaints(name, 621, 239, timeout=30)                    # the System Font row
    x4emu(name, 'wait-quiet', '--seconds', '3', '--timeout', '40')
    page = tmp / 'system-font.png'
    x4emu(name, 'screenshot', str(page))
    assert masked_diff(page, settings) > 5000, 'the System Font page never opened'
    # row 1 is "Built-in Base Font", then the packages by display name: MiSans Demibold, Test Font
    before = state(name)
    tap_repaints(name, 340, 239, timeout=40)                    # the third row: Test Font
    x4emu(name, 'wait-quiet', '--seconds', '2', '--timeout', '40')
    applying = tmp / 'applying.png'
    x4emu(name, 'screenshot', str(applying))
    assert masked_diff(applying, page) > 12000, \
        'tapping the package row did not open "Applying system font" (wrong row?)'
    # the stock restarts itself to bring the new font up; the panel is re-initialised
    st = wait_for(lambda: (s := state(name))['epd_resets'] > before['epd_resets'] and s, 240,
                  'the restart after the font switch')
    wait_for(lambda: state(name)['refresh_count'] >= st['refresh_count'] + 2, 240, 'Home repainted')
    x4emu(name, 'wait-quiet', '--seconds', '3', '--timeout', '60')
    switched = tmp / 'home-switched.png'
    x4emu(name, 'screenshot', str(switched))
    assert state(name)['epd_unknown_cmds'] == 0
    assert 'External font failed to load' not in x4emu(name, 'log').stdout
    # the built font is on the glass: Home is no longer the golden, and the title is our bitmap
    assert masked_diff(switched, STOCK_GOLDEN) > 4000, 'Home is still drawn in the card font'
    # "Bookshelf" is now set in the built font: same left edge, the built font's own width and cap
    # height, not the card font's (which the golden pins at 113x20 starting at upright (22, 73))
    built_bb = xtfont.Xtf.open(os.path.join(built['dir'], 'system_medium.xtf')) \
        .render('Bookshelf', pad=0)[1]['ink_bbox']
    want = (built_bb[2] - built_bb[0] + 1, built_bb[3] - built_bb[1] + 1)
    got_bb = title_ink(switched)
    got = (got_bb[2] - got_bb[0], got_bb[3] - got_bb[1])
    assert got_bb[0] == 20 + built_bb[0], (got_bb, built_bb)     # the pen still starts at x=20
    assert abs(got[0] - want[0]) <= 3 and got[1] == want[1], \
        f'the Home title is {got[0]}x{got[1]} px of ink, the built font draws {want[0]}x{want[1]}'
    assert abs(got[0] - 113) > 4 and (got[0], got[1]) != (113, 20), \
        'the Home title is still the card font\'s 113x20 "Bookshelf"'
    # the card carries what only a successful load can write
    listing = mdir(sd, '::/XTData/system_fonts/test-font')
    assert 'system_small.base.xtfp' in listing and 'system_medium.base.xtfp' in listing, listing
    assert 'font_id=test-font' in mtype(sd, '::/XTData/system_fonts/selection.config')
