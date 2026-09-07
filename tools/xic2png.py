#!/usr/bin/env python3
r"""Decode (and encode) `.xic`, the stock Xteink firmware's image container.

  xic2png.py IN.xic OUT.png                    decode a .xic into a PNG
  xic2png.py IN.xic [OUT.png] --info           print the header fields as JSON (OUT.png optional)
  xic2png.py IN.xic [OUT.png] --diff OTHER.png compare with a PNG; exit 1 when they differ
  xic2png.py --encode IN.png OUT.xic           the inverse: a PNG back into a mono .xic

`.xic` is what the stock app (`xteink_app` 7.2.4) writes for page caches, covers, font previews and
for its Developer -> Screen Capture screenshots (`/sdcard/screenshots/screenshot_%s.xic`), so a
capture taken in the emulator can be diffed against `x4emu screenshot` -- and a panel screenshot can
be turned into a `.xic` and put back on the card. Reaching Screen Capture needs developer mode:
`tools/stockdev.py` patches the 7.2.4 stub that gates it. The format was read out of the writer
(app 0x42026138 packs the payload, 0x422153ec / 0x42026347 fill the header) and confirmed against a
capture; docs/xic.md is the long version. A 24-byte little-endian header, then the raw pixels:

  0..3    magic "XIC\0"
  4..5    u16 width                     480 for a screen capture: the portrait UI frame
  6..7    u16 height                    800
  8       u8  format version            1 (the reader at 0x422153ec accepts 1 or 2)
  9       u8  levels                    1 = mono, 4 = grey
  10      u8  planes                    1 = mono, 2 = grey (one 1-bpp plane per bit)
  11      u8  0                         unknown, always 0 in every file seen
  12..15  u32 0                         must be 0 for version 1 (the reader rejects the file)
  16..19  u32 payload length            == stride * height * planes, stride = (width + 7) >> 3
  20..23  u32 0                         unknown, always 0 in every file seen

Byte 8 is the **format version, not a bit depth** -- the depth is bytes 9 and 10, and a grey file is
two appended 1-bpp planes, never packed pixel pairs. Both header words that must be zero and the
payload length are validated, so a file that means something else is rejected with a message instead
of being decoded into noise.

The pixel order was settled on images/device/sd-files/XTData/system_fonts/misans-demibold/preview.xic
(320x96, a MiSans DemiBold specimen) and re-confirmed on a screen capture: rows **top to bottom**,
each padded to a whole number of bytes, **MSB first** inside a byte, **1 = black** (the writer stores
the inverted pixel value). That is the only one of the four combinations that renders the specimen
("Small Aa 123 <CJK> / Medium Aa 123 <CJK>") as black text on white: LSB-first mirrors every group of
8 pixels and shreds the glyphs into columns, and 1 = white gives white on black (13.2 % of the bits
are set -- an ink fraction, not a paper fraction). All three assumptions are the constants below;
flipping one is how another container gets tested.

**Grey (`levels=4, planes=2`) is a documented guess.** The second plane holds bit 1 of the same
pixels, directly after the first, and the level is `bit0 | bit1 << 1` of the stored (inverted) bits,
rendered `255 - level * 85`. That is what the writer's disassembly says (`movi a9,1 .. movi a9,4` /
`movi a8,1 .. movi a8,2`, gated on a "display is grey" flag); no grey `.xic` has ever been seen, in
the emulator or on the card, so the plane order and the level mapping are unverified.

`--diff OTHER.png` counts the pixels that differ by more than DIFF_THRESHOLD after both images are
brought into the same frame, exactly as `x4emu screenshot --diff` does for CrossPoint's on-device
BMP: when the two sizes are each other's transpose, the portrait one is rotated back into the
landscape panel frame (`rotate(90, expand=True)`), which is what a 480x800 capture of the 800x480
panel needs. It prints "N% of pixels differ (n)" and exits 1 when n is not 0.

  .venv/bin/python tools/xic2png.py capture.xic capture.png --diff panel.png
        -> 0.000% of pixels differ (0)   for tests/data/stock-home-{capture.xic,panel.png}

`--encode` writes mono only (version 1, levels 1, planes 1; mode "1" after a threshold at 128, no
dithering), which round-trips a decoded mono .xic byte for byte. `decode_xic(data) -> (Image,
header)`, `encode_xic(image) -> bytes` and `compare(im1, im2) -> (pixels, percent)` are importable.
"""
import argparse, json, struct, sys

MAGIC = b'XIC\0'
HEADER = 24
VERSIONS = (1, 2)       # format versions the stock's own reader accepts (header byte 8)
MONO = (1, 1)           # (levels, planes) of a mono file                             (confirmed)
GREY = (4, 2)           # (levels, planes) of a 4-level grey file                     (GUESS)
MSB_FIRST = True        # the first pixel of a byte sits in its most significant bit  (confirmed)
ONE_IS_BLACK = True     # a set bit is ink, not paper                                 (confirmed)
TOP_DOWN = True         # the first row of the payload is the top row of the image    (confirmed)
DIFF_THRESHOLD = 64     # a gray difference above this counts as a differing pixel (as x4emu screenshot)
GREY_STEP = 85          # grey rendering: level L (0..3) -> 255 - L * 85              (GUESS)


class XicError(ValueError):
    """A .xic file that cannot be parsed (bad magic, impossible header, short payload)."""


def _rawmode():
    """Pillow raw mode for one 1-bpp plane under the constants above ('1;I' inverted, ';R' LSB)."""
    return '1' + (';' if ONE_IS_BLACK or not MSB_FIRST else '') + ('I' if ONE_IS_BLACK else '') \
        + ('R' if not MSB_FIRST else '')


def parse_header(data):
    """The 24-byte header as a dict, validated against len(data). Raises XicError.

    Keys are the header's own field names: magic, width, height, version, levels, planes, byte11,
    reserved12, payload_bytes, stride, reserved20, file_bytes."""
    if len(data) < HEADER:
        raise XicError(f'not a .xic file: {len(data)} bytes, shorter than the {HEADER}-byte header')
    if data[:4] != MAGIC:
        raise XicError(f'bad magic {bytes(data[:4])!r}, expected {MAGIC!r}')
    width, height, version, levels, planes, byte11 = struct.unpack('<HHBBBB', data[4:12])
    reserved12, payload, reserved20 = struct.unpack('<III', data[12:24])
    if not width or not height:
        raise XicError(f'bad dimensions {width}x{height}')
    if version not in VERSIONS:
        raise XicError(f'unsupported format version {version} (header byte 8; the stock reader '
                       f'accepts {" and ".join(map(str, VERSIONS))})')
    if (levels, planes) not in (MONO, GREY):
        raise XicError(f'unsupported levels={levels} planes={planes} (header bytes 9 and 10; only '
                       f'{MONO[0]}/{MONO[1]} mono and {GREY[0]}/{GREY[1]} grey exist)')
    if reserved12:
        raise XicError(f'header bytes 12..15 are {reserved12:#x}, not 0 (the stock reader rejects a '
                       f'version {version} file whose word there is set)')
    stride = (width + 7) >> 3
    if payload != stride * height * planes:
        raise XicError(f'payload length {payload} does not match {width}x{height} in {planes} '
                       f'plane(s) ({stride} bytes per row x {height} rows x {planes} planes = '
                       f'{stride * height * planes})')
    if len(data) - HEADER < payload:
        raise XicError(f'truncated: header declares {payload} payload bytes, file holds '
                       f'{len(data) - HEADER}')
    return {'magic': MAGIC.decode('latin1'), 'width': width, 'height': height, 'version': version,
            'levels': levels, 'planes': planes, 'byte11': byte11, 'reserved12': reserved12,
            'payload_bytes': payload, 'stride': stride, 'reserved20': reserved20,
            'file_bytes': len(data)}


def decode_xic(data):
    """(PIL.Image, header dict) for the bytes of a .xic file.

    Mono (levels 1, planes 1) gives mode "1" and is verified against a screen capture; grey
    (levels 4, planes 2) gives mode "L" through the two-plane guess of the module docstring."""
    from PIL import Image
    h = parse_header(data)
    size, stride, plane = (h['width'], h['height']), h['stride'], h['stride'] * h['height']
    payload = data[HEADER:HEADER + h['payload_bytes']]
    if h['planes'] == 1:
        im = Image.frombytes('1', size, payload[:plane], 'raw', _rawmode(), stride)
    else:
        im = _grey_planes(payload, size, stride, plane)
    if not TOP_DOWN:
        im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return im, h


def _grey_planes(payload, size, stride, plane):
    """The two-plane grey guess: bit 0 from the first plane, bit 1 from the second, level
    `bit0 | bit1 << 1` rendered as `255 - level * GREY_STEP`. Never observed in the wild."""
    from PIL import Image, ImageChops
    bits = [Image.frombytes('1', size, payload[i * plane:(i + 1) * plane], 'raw', _rawmode(), stride)
            for i in (0, 1)]
    # mode "1" is 255 for paper and 0 for ink, so "the bit is set" reads back as "the pixel is 0"
    lo = bits[0].convert('L').point(lambda v: 1 if v == 0 else 0)
    hi = bits[1].convert('L').point(lambda v: 2 if v == 0 else 0)
    return ImageChops.add(lo, hi).point(lambda level: 255 - level * GREY_STEP)


def encode_xic(image, levels=1, planes=1):
    """A PNG/PIL image as the bytes of a mono .xic (version 1, levels 1, planes 1; thresholded at
    128, no dithering). The grey path is a decode-only guess, so encoding it is refused."""
    from PIL import Image
    if (levels, planes) != MONO:
        raise ValueError(f'only mono ({MONO[0]} level, {MONO[1]} plane) encoding is implemented')
    if image.mode != '1':
        image = image.convert('L').point(lambda v: 255 if v >= 128 else 0).convert('1',
                                                                                  dither=Image.Dither.NONE)
    if not TOP_DOWN:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    width, height = image.size
    stride = (width + 7) >> 3
    payload = image.tobytes('raw', _rawmode(), stride)
    assert len(payload) == stride * height, (len(payload), stride, height)
    return (MAGIC + struct.pack('<HHBBBB', width, height, 1, levels, planes, 0) + b'\0' * 4
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
    ap.add_argument('--encode', action='store_true', help='PNG -> .xic (mono) instead of .xic -> PNG')
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
        print(f"{a.dst}: {h['width']}x{h['height']} v{h['version']} "
              f"{h['levels']} level / {h['planes']} plane, {len(blob)} bytes")
        if a.info:
            print(json.dumps(h, indent=2))
        return 0

    try:
        data = open(a.src, 'rb').read()
        im, h = decode_xic(data)
    except XicError as e:
        sys.exit(f'{a.src}: {e}')
    if a.info:
        print(json.dumps(h, indent=2))
    if a.dst:
        im.save(a.dst)
        if not a.info:
            print(f"{a.dst}: {h['width']}x{h['height']} v{h['version']} "
                  f"{h['levels']} level / {h['planes']} plane")
    if a.diff:
        n, pct = compare(im, Image.open(a.diff))
        print(f'{pct:.3f}% of pixels differ ({n})')
        return 1 if n else 0
    if not a.dst and not a.info:
        ap.error('nothing to do: give OUT.png, --info or --diff')
    return 0


if __name__ == '__main__':
    sys.exit(main())
