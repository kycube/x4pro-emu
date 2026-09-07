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

**What the stock does with a built package** (`test_stock_font_package_installs_and_renders`, and the
session's scratch boots ft1..ft5): a package with no `.hot.xtfp` is *not* refused -- switching to a
hot-less package whose `.xtf` files are the card's own bytes succeeds, the app writes its own
`system_{small,medium}.base.xtfp` next to them and rewrites `selection.config`, so `hot` is optional
exactly as `required_features` suggested. Our *generated* `.xtf` is a different matter: the stock
lists it correctly in Settings -> System Font (display name, style and "S/M 1169/1169 glyph" read out
of the package, so the manifest and both files are opened and their glyph counts trusted) but
switching to it ends in the toast "Failed to switch system font", and a boot with
`selection.config` already pointing at it falls back to the built-in base font without a word in the
console. Padding the range/advance tables out to the card font's own offsets (0x1a40 / 0x1c00) and
covering 35635 codepoints from Arial Unicode both failed the same way, so it is neither the table
layout nor the coverage. The remaining untested difference is the 8-byte hash at header 0x30, whose
algorithm is unknown. Until that is pinned, the emulator test asserts what is reproducible: the
package installs, the stock boots clean with it on the card, and its System Font page lists it.
"""
import hashlib, json, os, shutil, struct, subprocess, sys, zipfile
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


@pytest.fixture
def stock_font(tmp_path, request, built, stock_card):
    """`test_stock.stock` with the built package installed `--direct` on the card copy, so the
    stock boots with `selection.config` already pointing at it."""
    if not os.path.exists(test_stock.DUMP):
        pytest.skip('device dump not available')
    name = 'ft-' + request.node.name.replace('[', '-').replace(']', '').replace('=', '-')
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(test_stock.DUMP, flash)
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'set-u8',
                    'user_config', 'net_en', '0'], check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    xtfont.install_package(str(sd), built['report']['package'], direct=True)
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd), '--boot-hold-power')
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def test_stock_font_package_installs_and_renders(stock_font):
    """The stock boots with the built package selected on the card, refuses it, and says so on the
    glass: "External font failed to load. Using built-in font." is a **toast**, not a console line
    (that is why the console is asserted clean and the toast is looked for in the Home screenshot).
    The Home title is therefore still the built-in base font -- the same MiSans design as the card
    package, so the title alone cannot tell the two apart; the toast can. Settings -> System Font
    still lists the package with the name, style and glyph counts read out of it, so the manifest
    and both `.xtf` files parse. Once a generated `.xtf` is accepted, `toast` below goes to 0 and
    the Home comparison against tests/golden/stock-home.png becomes the assertion to flip."""
    from test_stock import boot_to_home, masked_diff, state, tap_repaints
    name, tmp = stock_font
    st = boot_to_home(name)
    assert st['epd_unknown_cmds'] == 0, st
    console = x4emu(name, 'log').stdout
    assert 'External font failed to load' not in console, 'the toast reached the console after all'
    home = tmp / 'home.png'
    x4emu(name, 'screenshot', str(home))
    toast = masked_diff(home, STOCK_GOLDEN)
    assert toast > 4000, ('Home matches the golden: the "External font failed to load" toast is '
                          'gone, so the generated .xtf may now be accepted -- check the screenshot')
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
    assert state(name)['epd_unknown_cmds'] == 0
    assert 'External font failed to load' not in x4emu(name, 'log').stdout
