"""tools/xic2png.py on two committed `.xic` files -- no emulator, no device dump, no QEMU.

tests/data/preview.xic is a copy of the stock's own font preview
(images/device/sd-files/XTData/system_fonts/misans-demibold/preview.xic, 320x96, 3864 bytes): a
MiSans DemiBold specimen reading "Small Aa 123 <CJK> / Medium Aa 123 <CJK>". It is what pinned the
three decode assumptions before any capture existed (docstring of the tool):

  MSB_FIRST     the strokes of the glyphs are horizontally continuous -- 0.6 % of the ink pixels
                have no ink neighbour left or right, against 3.9 % when the bits of every byte are
                read the other way round (that decode mirrors each group of 8 columns)
  ONE_IS_BLACK  13.2 % of the bits are set: an ink fraction on paper, not paper on ink
  TOP_DOWN      the shorter "Small" line sits above the taller "Medium" line

tests/data/stock-home-{capture.xic,panel.png} is the oracle that settled the header: a Developer ->
Screen Capture `.xic` the stock wrote to the emulator's card, and the `x4emu screenshot` of the same
Home screen taken just before it. The two agree in **every** pixel, which is what fixes the
orientation (portrait 480x800, the landscape panel rotated 90 degrees counter-clockwise) as well as
the polarity and the bit order at screen scale. Both files are emulator-made and hold nothing of the
owner's. `tests/test_stock_capture.py` reproduces that pair from a boot; this file only needs the
bytes.

Header bytes 8, 9 and 10 are version / levels / planes -- byte 8 is *not* a bit depth, and grey is
two appended 1-bpp planes rather than packed pixel pairs (docs/xic.md). The tests below check that
the fields are read as such, that a file disagreeing with itself is rejected with a message rather
than decoded into noise, and that the grey path does what the tool's docstring says it guesses (no
grey `.xic` has ever been seen, so that is all it can check).
"""
import json, os, struct, subprocess, sys
import pytest
from conftest import ROOT, PY

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import xic2png                                                                 # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'xic2png.py')
DATA = os.path.join(ROOT, 'tests', 'data')
SAMPLE = os.path.join(DATA, 'preview.xic')
CAPTURE = os.path.join(DATA, 'stock-home-capture.xic')
PANEL = os.path.join(DATA, 'stock-home-panel.png')
Image = pytest.importorskip('PIL.Image')


def run(*args, expect=0):
    """`tools/xic2png.py ...`; returns stdout, asserting the exit code."""
    r = subprocess.run([PY, TOOL, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, f'xic2png {args} exited {r.returncode}, wanted {expect}:\n{r.stdout}\n{r.stderr}'
    return r.stdout


def header(width, height, version=1, levels=1, planes=1, byte11=0, reserved12=0, payload=None,
           reserved20=0):
    """A synthetic 24-byte header, so one field at a time can be broken."""
    if payload is None:
        payload = ((width + 7) >> 3) * height * planes
    return (b'XIC\0' + struct.pack('<HHBBBB', width, height, version, levels, planes, byte11)
            + struct.pack('<III', reserved12, payload, reserved20))


def ink(im):
    """[[True where the pixel is black]] plus the number of black pixels."""
    px = im.convert('L').load()
    w, h = im.size
    rows = [[px[x, y] < 128 for x in range(w)] for y in range(h)]
    return rows, sum(map(sum, rows))


def isolated(rows):
    """Ink pixels with no ink pixel to their left or right -- the fingerprint of a wrong bit order."""
    w = len(rows[0])
    return sum(1 for r in rows for x in range(w)
               if r[x] and not (x and r[x - 1]) and not (x < w - 1 and r[x + 1]))


def test_the_sample_header_is_read_as_320x96_mono():
    """Every header field of the font preview, by its real name: version 1, one level, one plane,
    and the three reserved fields all zero."""
    data = open(SAMPLE, 'rb').read()
    assert len(data) == 3864 and data[:4] == b'XIC\0'
    im, h = xic2png.decode_xic(data)
    assert (h['width'], h['height']) == (320, 96) and im.size == (320, 96)
    assert (h['version'], h['levels'], h['planes']) == (1, 1, 1)
    assert h['payload_bytes'] == 3840 == h['stride'] * h['height'] * h['planes'] == 320 * 96 // 8
    assert h['byte11'] == h['reserved12'] == h['reserved20'] == 0
    assert (h['magic'], h['file_bytes']) == ('XIC\0', 3864)
    assert im.mode == '1'


def test_the_sample_decodes_to_black_text_on_white():
    """The image itself: black and white both present, an ink fraction a page of text can have, two
    lines of text with the shorter one on top, and strokes that hold together horizontally."""
    im, _ = xic2png.decode_xic(open(SAMPLE, 'rb').read())
    rows, black = ink(im)
    w, h = im.size
    frac = 100.0 * black / (w * h)
    assert 3.0 < frac < 60.0, f'{frac:.1f} % black is not text on paper'
    assert 0 < black < w * h, 'the image is one flat colour'

    # two bands of ink with white margins around them: the two specimen lines
    inked = [y for y, r in enumerate(rows) if any(r)]
    bands, start = [], inked[0]
    for prev, y in zip(inked, inked[1:]):
        if y != prev + 1:
            bands.append((start, prev)); start = y
    bands.append((start, inked[-1]))
    assert len(bands) == 2, f'expected the "Small" and "Medium" lines, got bands {bands}'
    assert bands[0][0] > 0 and bands[-1][1] < h - 1, 'no top/bottom margin'
    # TOP_DOWN: "Small ..." (20 rows tall) comes before "Medium ..." (24 rows); a flipped payload
    # would put the taller line first
    assert bands[0][1] - bands[0][0] < bands[1][1] - bands[1][0], f'lines in the wrong order: {bands}'
    assert not any(rows[y][0] or rows[y][-1] for y in range(h)), 'ink in the left/right margin'

    # MSB_FIRST: reading the bits the other way mirrors every group of 8 columns and leaves far more
    # ink pixels stranded without a horizontal neighbour
    mirrored = Image.frombytes('1', (w, h), open(SAMPLE, 'rb').read()[24:], 'raw', '1;IR')
    assert isolated(rows) < 0.015 * black, 'the strokes are broken up: bit order?'
    assert isolated(ink(mirrored)[0]) > 2 * isolated(rows), 'the two bit orders are indistinguishable here'


def test_the_stock_screen_capture_matches_the_panel_screenshot_exactly():
    """The oracle: a 480x800 Developer -> Screen Capture `.xic` of Home and the 800x480 panel PNG of
    the same screen. Header fields by name, and 0 pixels differing once the capture is rotated back
    into the landscape frame -- not "close", but byte for byte after the rotation."""
    data = open(CAPTURE, 'rb').read()
    assert len(data) == 48024
    im, h = xic2png.decode_xic(data)
    assert (h['width'], h['height'], im.size) == (480, 800, (480, 800))
    assert (h['version'], h['levels'], h['planes']) == (1, 1, 1)
    assert h['payload_bytes'] == 48000 == h['stride'] * h['height'] * h['planes'] == 60 * 800
    assert h['byte11'] == h['reserved12'] == h['reserved20'] == 0
    assert h['file_bytes'] == 24 + 48000

    panel = Image.open(PANEL)
    assert panel.size == (800, 480), 'the panel screenshot is the landscape frame'
    assert xic2png.compare(im, panel) == (0, 0.0), 'the capture and the panel disagree'
    rotated = im.transpose(Image.Transpose.ROTATE_90)             # 90 degrees counter-clockwise
    assert rotated.size == (800, 480)
    assert rotated.convert('L').tobytes() == panel.convert('L').tobytes(), 'rotation convention'


def test_cli_diff_of_the_capture_against_the_panel_exits_zero(tmp_path):
    """The same pair through the CLI, which is how a capture gets checked in a shell."""
    out = run(CAPTURE, tmp_path / 'home.png', '--diff', PANEL)
    assert '0.000% of pixels differ (0)' in out, out
    assert Image.open(tmp_path / 'home.png').size == (480, 800)


def test_round_trip_png_xic_png_is_identical(tmp_path):
    """PNG -> .xic -> PNG pixel for pixel, and the re-encoded file equals the original .xic byte for
    byte (both samples are mono, so nothing is lost in either direction)."""
    for src in (SAMPLE, CAPTURE):
        blob = open(src, 'rb').read()
        im, _ = xic2png.decode_xic(blob)
        png = tmp_path / (os.path.basename(src) + '.png')
        im.save(png)

        again = xic2png.encode_xic(Image.open(png))
        assert again == blob, f'the encoder does not reproduce {os.path.basename(src)}'
        h = xic2png.parse_header(again)
        assert (h['version'], h['levels'], h['planes']) == (1, 1, 1), 'encode writes v1 mono'

        out = tmp_path / 'rt.xic'
        run('--encode', png, out)
        back = tmp_path / 'back.png'
        run(out, back)
        assert Image.open(back).convert('L').tobytes() == im.convert('L').tobytes()

    # a grayscale source is thresholded at 128, so the round trip is still exact for it
    im, _ = xic2png.decode_xic(open(SAMPLE, 'rb').read())
    gray = im.convert('L').point(lambda v: 200 if v else 40)
    assert xic2png.encode_xic(gray) == open(SAMPLE, 'rb').read()


def test_a_broken_header_is_rejected_with_a_message(tmp_path):
    """Bad magic, a truncated payload, a header shorter than 24 bytes, an unknown version, an
    impossible levels/planes pair, a non-zero word at 12..15 and a payload length that does not match
    the dimensions: XicError (a ValueError), and the CLI exits non-zero with the reason on stderr
    instead of a traceback."""
    blob = open(SAMPLE, 'rb').read()
    body = blob[24:]
    with pytest.raises(xic2png.XicError, match='bad magic'):
        xic2png.decode_xic(b'PNG\0' + blob[4:])
    with pytest.raises(xic2png.XicError, match='truncated'):
        xic2png.decode_xic(blob[:-1])
    with pytest.raises(xic2png.XicError, match='shorter than'):
        xic2png.decode_xic(blob[:20])
    with pytest.raises(xic2png.XicError, match='bad dimensions'):
        xic2png.decode_xic(header(0, 96) + body)
    with pytest.raises(xic2png.XicError, match='format version 3'):
        xic2png.decode_xic(header(320, 96, version=3) + body)
    with pytest.raises(xic2png.XicError, match='levels=1 planes=2'):
        xic2png.decode_xic(header(320, 96, levels=1, planes=2) + body * 2)
    with pytest.raises(xic2png.XicError, match='levels=4 planes=1'):
        xic2png.decode_xic(header(320, 96, levels=4, planes=1) + body)
    with pytest.raises(xic2png.XicError, match=r'bytes 12\.\.15'):
        xic2png.decode_xic(header(320, 96, reserved12=1) + body)
    with pytest.raises(xic2png.XicError, match='does not match'):
        xic2png.decode_xic(header(321, 96, payload=3840) + body)
    # version 2 and a set byte 11 / word 20 stay acceptable: the stock's own reader takes them
    assert xic2png.decode_xic(header(320, 96, version=2, byte11=7, reserved20=9) + body)[1]['byte11'] == 7

    bad = tmp_path / 'bad.xic'
    bad.write_bytes(b'XIC\0' + blob[4:-100])
    r = subprocess.run([PY, TOOL, str(bad), str(tmp_path / 'x.png')], capture_output=True, text=True)
    assert r.returncode == 1 and 'truncated' in r.stderr and 'Traceback' not in r.stderr, r.stderr
    assert not (tmp_path / 'x.png').exists()


def test_cli_info_prints_the_header_fields_by_name():
    """--info is exactly the twelve header fields, no more (docs/xic.md's table)."""
    h = json.loads(run(CAPTURE, '--info'))
    assert list(h) == ['magic', 'width', 'height', 'version', 'levels', 'planes', 'byte11',
                       'reserved12', 'payload_bytes', 'stride', 'reserved20', 'file_bytes']
    assert (h['width'], h['height'], h['version'], h['levels'], h['planes']) == (480, 800, 1, 1, 1)
    assert (h['payload_bytes'], h['stride'], h['file_bytes']) == (48000, 60, 48024)
    assert h['magic'] == 'XIC\0'
    assert json.loads(run(SAMPLE, '--info'))['file_bytes'] == 3864


def test_cli_diff_counts_the_differing_pixels(tmp_path):
    """`--diff OTHER.png`: exit 0 and 0 pixels for the PNG the tool just wrote, exit 1 and exactly
    one pixel after flipping one pixel of it (the same `x4emu screenshot --diff` wording)."""
    same = tmp_path / 'same.png'
    run(SAMPLE, same)
    out = run(SAMPLE, '--diff', same)
    assert '0.000% of pixels differ (0)' in out, out

    one = Image.open(same).convert('L')
    px = one.load()
    px[0, 0] = 0 if px[0, 0] else 255                       # the corner is white in the sample
    changed = tmp_path / 'changed.png'
    one.save(changed)
    out = run(SAMPLE, '--diff', changed, expect=1)
    assert 'of pixels differ (1)' in out, out
    assert xic2png.compare(Image.open(same), Image.open(changed)) == (1, 100.0 / (320 * 96))


def test_a_portrait_capture_is_un_rotated_into_the_landscape_frame():
    """A .xic written in the portrait device frame (480x800) is compared against the 800x480 panel
    screenshot after the same rotate(90, expand=True) x4emu screenshot --diff uses for the BMP."""
    im, _ = xic2png.decode_xic(open(SAMPLE, 'rb').read())
    land = im.convert('L')
    port = land.rotate(-90, expand=True)                     # what the device would have stored
    assert port.size == (96, 320)
    assert xic2png.compare(land, port)[0] == 0               # the transpose is rotated back, not resized
    assert xic2png.compare(port, land)[0] == 0               # either way round
    assert xic2png.compare(land, land.resize((160, 48)))[0] > 0   # a plain resize still compares


def test_grey_is_decoded_as_two_appended_planes():
    """The grey branch (levels 4, planes 2): plane 0 holds bit 0 of every pixel, plane 1 holds bit 1
    right after it, and the level `bit0 | bit1 << 1` renders as 255 - level * 85. This is the tool's
    documented guess -- no grey .xic has ever been seen -- so the test only pins what the tool does,
    not what the device writes."""
    plane0 = bytes([0b10100000, 0])                          # bit 0 set for pixels 0 and 2 of row 0
    plane1 = bytes([0b11000000, 0])                          # bit 1 set for pixels 0 and 1 of row 0
    blob = header(8, 2, levels=4, planes=2) + plane0 + plane1
    im, h = xic2png.decode_xic(blob)
    assert (h['levels'], h['planes'], h['stride'], h['payload_bytes']) == (4, 2, 1, 4)
    assert (im.mode, im.size) == ('L', (8, 2))
    assert list(im.tobytes()) == [0, 85, 170, 255, 255, 255, 255, 255] + [255] * 8

    # the first plane on its own is a mono image, not "the first half of the grey one"
    mono, _ = xic2png.decode_xic(header(8, 2) + plane0)
    assert list(mono.convert('L').tobytes()) == [0, 255, 0, 255, 255, 255, 255, 255] + [255] * 8

    with pytest.raises(xic2png.XicError, match='does not match'):
        xic2png.decode_xic(header(8, 2, levels=4, planes=2, payload=2) + plane0)
    with pytest.raises(ValueError, match='only mono'):
        xic2png.encode_xic(im, levels=4, planes=2)
