#!/usr/bin/env python3
r"""Decode (and encode) `.xic`, the stock Xteink firmware's image container.

  xic2png.py IN.xic OUT.png                    decode a .xic into a PNG
  xic2png.py IN.xic [OUT.png] --info           print the header fields as JSON (OUT.png optional)
  xic2png.py IN.xic [OUT.png] --diff OTHER.png compare with a PNG; exit 1 when they differ
  xic2png.py --encode IN.png OUT.xic           the inverse: a PNG back into a 1 bpp .xic

`.xic` is what the stock app (`xteink_app` 7.2.4) writes for page caches, covers, font previews and
for its Developer -> Screen Capture screenshots (`/sdcard/screenshots/screenshot_%s.xic`), so a
capture taken in the emulator can be diffed against `x4emu screenshot` -- and a panel screenshot can
be turned into a `.xic` and put back on the card. A 24-byte header, then the raw pixels:

  0..3    magic "XIC\0"
  4..5    u16le width
  6..7    u16le height
  8       bits per pixel (1 in the confirmed sample; 2 = 4 gray levels is handled, unconfirmed)
  9       format / planes (1)
  10      version (1)
  11      0
  12..15  0
  16..19  u32le payload length, == height * ceil(width * bpp / 8)
  20..23  0

Bytes 8..11 read `01 01 01 00` in the sample. Only byte 8 is read (as bits per pixel) and it must
agree with the payload length, so a file that means something else there is rejected with a message
instead of being decoded into noise; bytes 9..10 are carried into `--info` untouched.

The pixel order was settled on images/device/sd-files/XTData/system_fonts/misans-demibold/preview.xic
(320x96, a MiSans DemiBold specimen): rows **top to bottom**, each padded to a whole number of bytes,
**MSB first** inside a byte, **1 = black**. That is the only one of the four combinations that
renders the specimen ("Small Aa 123 <CJK> / Medium Aa 123 <CJK>") as black text on white: LSB-first
mirrors every group of 8 pixels and shreds the glyphs into columns, and 1 = white gives white on
black (13.2 % of the bits are set -- an ink fraction, not a paper fraction). All three assumptions
are the constants below; flipping one is how another container gets tested.

`--diff OTHER.png` counts the pixels that differ by more than DIFF_THRESHOLD after both images are
brought into the same frame, exactly as `x4emu screenshot --diff` does for CrossPoint's on-device
BMP: when the two sizes are each other's transpose, the portrait one is rotated back into the
landscape panel frame (`rotate(90, expand=True)`), which is what a 480x800 capture of the 800x480
panel needs. It prints "N% of pixels differ (n)" and exits 1 when n is not 0.

  .venv/bin/python tools/xic2png.py capture.xic capture.png --diff panel.png

`--encode` writes 1 bpp only (mode "1" after a threshold at 128, no dithering), which round-trips a
decoded .xic byte for byte. The 2 bpp decode packs four pixels per byte, most significant pair
first, level 0 = white .. 3 = black (the 1 bpp polarity, scaled) -- a guess until a 2 bpp file is
seen. `decode_xic(data) -> (Image, header)` and `encode_xic(image) -> bytes` are importable.
"""
import argparse, json, os, struct, sys

MAGIC = b'XIC\0'
HEADER = 24
MSB_FIRST = True        # the first pixel of a byte sits in its most significant bit  (confirmed, 1 bpp)
ONE_IS_BLACK = True     # a set bit is ink, not paper                                 (confirmed, 1 bpp)
TOP_DOWN = True         # the first row of the payload is the top row of the image    (confirmed, 1 bpp)
DIFF_THRESHOLD = 64     # a gray difference above this counts as a differing pixel (as x4emu screenshot)
GRAY_2BPP = (255, 170, 85, 0)   # 2 bpp level -> gray, level 0 = white .. 3 = black       (GUESS)


class XicError(ValueError):
    """A .xic file that cannot be parsed (bad magic, impossible header, short payload)."""


def _rawmode():
    """Pillow raw mode for 1 bpp under the constants above ('1', '1;I' inverted, ';R' LSB first)."""
    return '1' + (';' if ONE_IS_BLACK or not MSB_FIRST else '') + ('I' if ONE_IS_BLACK else '') \
        + ('R' if not MSB_FIRST else '')


def parse_header(data):
    """The 24-byte header as a dict, validated against len(data). Raises XicError."""
    if len(data) < HEADER:
        raise XicError(f'not a .xic file: {len(data)} bytes, shorter than the {HEADER}-byte header')
    if data[:4] != MAGIC:
        raise XicError(f'bad magic {bytes(data[:4])!r}, expected {MAGIC!r}')
    width, height, bpp, fmt, version, pad11 = struct.unpack('<HHBBBB', data[4:12])
    payload = struct.unpack('<I', data[16:20])[0]
    if not width or not height:
        raise XicError(f'bad dimensions {width}x{height}')
    if bpp not in (1, 2):
        raise XicError(f'unsupported bits per pixel {bpp} (header byte 8; 1 and 2 are implemented)')
    stride = (width * bpp + 7) // 8
    if payload != stride * height:
        raise XicError(f'payload length {payload} does not match {width}x{height} at {bpp} bpp '
                       f'({stride} bytes per row x {height} rows = {stride * height})')
    if len(data) - HEADER < payload:
        raise XicError(f'truncated: header declares {payload} payload bytes, file holds '
                       f'{len(data) - HEADER}')
    return {'width': width, 'height': height, 'bpp': bpp, 'format': fmt, 'version': version,
            'pad11': pad11, 'payload_bytes': payload, 'stride': stride,
            'reserved_12_15': data[12:16].hex(), 'reserved_20_23': data[20:24].hex()}


def decode_xic(data):
    """(PIL.Image, header dict) for the bytes of a .xic file. 1 bpp gives mode "1", 2 bpp mode "L"."""
    from PIL import Image
    h = parse_header(data)
    payload = data[HEADER:HEADER + h['payload_bytes']]
    if h['bpp'] == 1:
        im = Image.frombytes('1', (h['width'], h['height']), payload, 'raw', _rawmode())
    else:
        im = Image.frombytes('L', (h['width'], h['height']), _gray_2bpp(payload, h), 'raw', 'L')
    if not TOP_DOWN:
        im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return im, h


def _gray_2bpp(payload, h):
    """2 bpp payload -> one gray byte per pixel (row padding dropped). GRAY_2BPP is a guess."""
    order = range(6, -2, -2) if MSB_FIRST else range(0, 8, 2)
    table = [bytes(GRAY_2BPP[(b >> s) & 3] for s in order) for b in range(256)]
    rows = []
    for y in range(h['height']):
        row = payload[y * h['stride']:(y + 1) * h['stride']]
        rows.append(b''.join(table[b] for b in row)[:h['width']])
    return b''.join(rows)


def encode_xic(image, bpp=1):
    """A PNG/PIL image as the bytes of a 1 bpp .xic (thresholded at 128, no dithering)."""
    from PIL import Image
    if bpp != 1:
        raise ValueError('only 1 bpp encoding is implemented')
    if image.mode != '1':
        image = image.convert('L').point(lambda v: 255 if v >= 128 else 0).convert('1',
                                                                                  dither=Image.Dither.NONE)
    if not TOP_DOWN:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    width, height = image.size
    payload = image.tobytes('raw', _rawmode())
    stride = (width + 7) // 8
    assert len(payload) == stride * height, (len(payload), stride, height)
    return (MAGIC + struct.pack('<HHBBBB', width, height, 1, 1, 1, 0) + b'\0' * 4
            + struct.pack('<I', len(payload)) + b'\0' * 4 + payload)


# ---------------------------------------------------------------- comparison
def compare(im1, im2, threshold=DIFF_THRESHOLD):
    """(differing pixels, percent) between two images, brought into one frame first: a portrait
    image whose size is the other's transpose is un-rotated into the landscape panel frame the way
    `x4emu screenshot --diff` un-rotates the device's 480x800 BMP; anything else is resized."""
    from PIL import Image, ImageChops
    im1 = im1.convert('L'); im2 = im2.convert('L')
    if im2.size == (im1.size[1], im1.size[0]):
        if im2.size[1] > im2.size[0]:
            im2 = im2.rotate(90, expand=True)
        else:
            im1 = im1.rotate(90, expand=True)
    if im2.size != im1.size:
        im2 = im2.resize(im1.size)
    diff = ImageChops.difference(im1, im2).point(lambda v: 255 if v > threshold else 0)
    n = diff.histogram()[255]
    return n, 100.0 * n / (im1.size[0] * im1.size[1])


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', help='the .xic to decode (or, with --encode, the PNG to encode)')
    ap.add_argument('dst', nargs='?', help='the PNG to write (or, with --encode, the .xic)')
    ap.add_argument('--encode', action='store_true', help='PNG -> .xic (1 bpp) instead of .xic -> PNG')
    ap.add_argument('--info', action='store_true', help='print the header fields as JSON')
    ap.add_argument('--diff', metavar='OTHER.png', help='compare the decoded image with a PNG; exit 1 when they differ')
    a = ap.parse_args(argv)
    from PIL import Image

    if a.encode:
        if not a.dst:
            ap.error('--encode needs IN.png OUT.xic')
        if a.diff:
            ap.error('--diff works on a decode, not on --encode')
        blob = encode_xic(Image.open(a.src))
        with open(a.dst, 'wb') as f:
            f.write(blob)
        h = parse_header(blob)
        print(f"{a.dst}: {h['width']}x{h['height']} {h['bpp']} bpp, {len(blob)} bytes")
        if a.info:
            print(json.dumps(h, indent=2))
        return 0

    try:
        data = open(a.src, 'rb').read()
        im, h = decode_xic(data)
    except XicError as e:
        sys.exit(f'{a.src}: {e}')
    if a.info:
        print(json.dumps({'path': os.path.abspath(a.src), 'magic': MAGIC.decode('latin1'),
                          'file_bytes': len(data), **h}, indent=2))
    if a.dst:
        im.save(a.dst)
        if not a.info:
            print(f"{a.dst}: {h['width']}x{h['height']} {h['bpp']} bpp")
    if a.diff:
        n, pct = compare(im, Image.open(a.diff))
        print(f'{pct:.3f}% of pixels differ ({n})')
        return 1 if n else 0
    if not a.dst and not a.info:
        ap.error('nothing to do: give OUT.png, --info or --diff')
    return 0


if __name__ == '__main__':
    sys.exit(main())
