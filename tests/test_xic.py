"""tools/xic2png.py on tests/data/preview.xic -- no emulator, no device dump, no QEMU.

tests/data/preview.xic is a copy of the stock's own font preview
(images/device/sd-files/XTData/system_fonts/misans-demibold/preview.xic, 320x96, 3864 bytes): a
MiSans DemiBold specimen reading "Small Aa 123 <CJK> / Medium Aa 123 <CJK>". It is the only `.xic`
whose contents are known, so it is what pins the three decode assumptions (docstring of the tool):

  MSB_FIRST     the strokes of the glyphs are horizontally continuous -- 0.6 % of the ink pixels
                have no ink neighbour left or right, against 3.9 % when the bits of every byte are
                read the other way round (that decode mirrors each group of 8 columns)
  ONE_IS_BLACK  13.2 % of the bits are set: an ink fraction on paper, not paper on ink
  TOP_DOWN      the shorter "Small" line sits above the taller "Medium" line

The `.xic` header is 24 bytes and only byte 8 (bits per pixel) is interpreted; the tests below check
that a file disagreeing with itself is rejected with a message rather than decoded into noise. 2 bpp
is a documented guess (no such file has been seen), so it is exercised on a synthetic header only.
"""
import json, os, struct, subprocess, sys
import pytest
from conftest import ROOT, PY

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import xic2png                                                                 # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'xic2png.py')
SAMPLE = os.path.join(ROOT, 'tests', 'data', 'preview.xic')
Image = pytest.importorskip('PIL.Image')


def run(*args, expect=0):
    """`tools/xic2png.py ...`; returns stdout, asserting the exit code."""
    r = subprocess.run([PY, TOOL, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, f'xic2png {args} exited {r.returncode}, wanted {expect}:\n{r.stdout}\n{r.stderr}'
    return r.stdout


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


def test_the_sample_header_is_read_as_320x96_1bpp():
    """Every header field of the known file, including the three bytes at 8..11 that are still a
    guess (bpp / format / version) and the reserved words, which are all zero in it."""
    data = open(SAMPLE, 'rb').read()
    assert len(data) == 3864 and data[:4] == b'XIC\0'
    im, h = xic2png.decode_xic(data)
    assert (h['width'], h['height']) == (320, 96) and im.size == (320, 96)
    assert (h['bpp'], h['format'], h['version'], h['pad11']) == (1, 1, 1, 0)
    assert h['payload_bytes'] == 3840 == h['stride'] * h['height'] == 320 * 96 // 8
    assert h['reserved_12_15'] == h['reserved_20_23'] == '00000000'
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


def test_round_trip_png_xic_png_is_identical(tmp_path):
    """PNG -> .xic -> PNG pixel for pixel, and the re-encoded file equals the original .xic byte for
    byte (the sample is 1 bpp, so nothing is lost in either direction)."""
    blob = open(SAMPLE, 'rb').read()
    im, _ = xic2png.decode_xic(blob)
    png = tmp_path / 'preview.png'
    im.save(png)

    again = xic2png.encode_xic(Image.open(png))
    assert again == blob, 'the encoder does not reproduce the sample'

    out = tmp_path / 'rt.xic'
    run('--encode', png, out)
    back = tmp_path / 'back.png'
    run(out, back)
    assert Image.open(back).convert('L').tobytes() == im.convert('L').tobytes()

    # a grayscale source is thresholded at 128, so the round trip is still exact for it
    gray = im.convert('L').point(lambda v: 200 if v else 40)
    assert xic2png.encode_xic(gray) == blob


def test_a_broken_header_is_rejected_with_a_message(tmp_path):
    """Bad magic, a truncated payload, a header shorter than 24 bytes, an unknown bpp and a payload
    length that does not match the dimensions: XicError (a ValueError), and the CLI exits non-zero
    with the reason on stderr instead of a traceback."""
    blob = open(SAMPLE, 'rb').read()
    with pytest.raises(xic2png.XicError, match='bad magic'):
        xic2png.decode_xic(b'PNG\0' + blob[4:])
    with pytest.raises(xic2png.XicError, match='truncated'):
        xic2png.decode_xic(blob[:-1])
    with pytest.raises(xic2png.XicError, match='shorter than'):
        xic2png.decode_xic(blob[:20])
    with pytest.raises(xic2png.XicError, match='bits per pixel 4'):
        xic2png.decode_xic(blob[:8] + bytes([4]) + blob[9:])
    with pytest.raises(xic2png.XicError, match='does not match'):
        xic2png.decode_xic(blob[:4] + struct.pack('<HH', 321, 96) + blob[8:])
    with pytest.raises(xic2png.XicError, match='bad dimensions'):
        xic2png.decode_xic(blob[:4] + struct.pack('<HH', 0, 96) + blob[8:])

    bad = tmp_path / 'bad.xic'
    bad.write_bytes(b'XIC\0' + blob[4:-100])
    r = subprocess.run([PY, TOOL, str(bad), str(tmp_path / 'x.png')], capture_output=True, text=True)
    assert r.returncode == 1 and 'truncated' in r.stderr and 'Traceback' not in r.stderr, r.stderr
    assert not (tmp_path / 'x.png').exists()


def test_cli_info_prints_the_header_as_json():
    out = run(SAMPLE, '--info')
    h = json.loads(out)
    assert h['width'] == 320 and h['height'] == 96 and h['bpp'] == 1
    assert h['magic'] == 'XIC\0' and h['file_bytes'] == 3864 and h['path'] == os.path.abspath(SAMPLE)


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


def test_2bpp_is_decoded_as_four_gray_levels():
    """The 2 bpp branch (four levels, two bits per pixel, most significant pair first, 0 = white ..
    3 = black) on a synthetic header -- a guess until a real 2 bpp .xic turns up, so this test only
    pins what the tool currently does."""
    payload = bytes([0b00_01_10_11, 0b11_11_00_00,          # 8x2: white..black, black x2, white x2
                     0b11_10_01_00, 0b00_00_11_11])
    blob = (b'XIC\0' + struct.pack('<HHBBBB', 8, 2, 2, 1, 1, 0) + b'\0' * 4
            + struct.pack('<I', len(payload)) + b'\0' * 4 + payload)
    im, h = xic2png.decode_xic(blob)
    assert (h['bpp'], h['stride'], im.mode, im.size) == (2, 2, 'L', (8, 2))
    assert list(im.tobytes()) == [255, 170, 85, 0, 0, 0, 255, 255,
                                 0, 85, 170, 255, 255, 255, 0, 0]
    with pytest.raises(xic2png.XicError, match='does not match'):
        xic2png.decode_xic(blob[:8] + b'\1' + blob[9:])      # 4 bytes cannot be 8x2 at 1 bpp
    with pytest.raises(ValueError, match='only 1 bpp'):
        xic2png.encode_xic(im, bpp=2)
