#!/usr/bin/env python3
r"""Read and write the stock Xteink firmware's system-font package (`.xtf` v2 + `.xtfont` ZIP).

  xtfont.py dump IN.xtf OUT.png [--text "..."] [--glyph CP] [--scale N] [--info]
  xtfont.py build TTF --id ID --name "Display Name" OUT_DIR [--small-size N --body-size N]
  xtfont.py install SD.img PACKAGE.xtfont [--direct]

`xteink_app` 7.2.4 has **no FreeType**: every glyph it draws is a pre-rendered 1-bpp bitmap
(`docs/stock-firmware.md` §4). With `mode=external` the system font is a pair of `.xtf` v2 files on
the card -- `system_small.xtf` (cell 20x20) and `system_medium.xtf` (cell 24x24) -- bound by
`XTData/system_fonts/selection.config` and installed from a `.xtfont` ZIP that the app picks up in
`XTCache/system_font_downloads/`. So a new system font is a **data-only** change: rasterise a TTF
into those cells, write the two files, the manifest and the package, and the stock installs it.

`.xtf` v2 (little-endian; the card's `misans-demibold` is the reference for every "copied" value):

    0x00  magic "XTF0"
    0x04  u16 version              2
    0x06  u16                      0x140            (COPIED, meaning unknown)
    0x08  u16                      3                (COPIED, meaning unknown)
    0x0a  u8 cell_w, u8 cell_h     20,20 / 24,24
    0x0c  u8 advance_y, u8         18,20 / 22,24    (advance_y = cell - 2; byte 0x0d = cell_h)
    0x0e  u8, u8                   11,2 / 13,2      (COPIED, meaning unknown)
    0x10  u16 line height, i16 descent   21,-6 / 26,-7   (COPIED)
    0x14  u32 range count          416 in both card files -- a *count*, not the "end" the survey guessed
    0x18  u32 glyph_count          43101
    0x1c  u32 range table offset   0x40
    0x20  u32                      0
    0x24  u32 glyph records offset 0x1c00
    0x28  u32 glyph record size    0x40 / 0x4c      (4 metric bytes + cell_h rows of (cell_w+7)//8)
    0x2c  u32 glyph data bytes     glyph_count * record size -- the file ends there, so the survey's
                                   "tail section offset (size 0x1c00)" is this length, and the 0x1c00
                                   is the *gap* in front of the records, not a trailer
    0x30  u32 crc32 of the glyph records            zlib CRC-32 of `data[glyph_offset:+glyph_bytes]`
    0x34  u32 crc32 of the header so far            zlib CRC-32 of `data[0:0x34]` -- the 52 bytes in
                                                    front of this field, so it covers 0x30 as well
    0x38  u32 advance table offset, u32 count       0x1a40, 95  (u8 advance_x for U+0020..U+007E)

Range table entries are 16 bytes, sorted by codepoint: `u32 first_codepoint, u32 count, u32
first_glyph_index, u16 0, u16 count` (the trailing u16 repeats the count in every entry of both card
files). Glyph records follow in glyph-index order.

**The four metric bytes are `advance_x, advance_y, x_offset, y_offset`** -- offsets signed (int8),
`advance_y` a constant equal to the header's. Pinned three ways against the card's fonts and the
emulator's Home screen (`dump` is what did it):

  * byte 0 equals the 95-entry advance table at 0x1a40 for **all** of U+0020..U+007E in both files;
  * byte 1 is 18 in every one of the 43101 records of the 20-px font and 22 in every record of the
    24-px one -- the header's `advance_y`, so it cannot be a per-glyph height;
  * bytes 2 and 3 place the ink: `.` has y_offset 13 and `B` has 1 in the 20-px font (both bottom out
    on the same baseline, row 17), and `j` has x_offset 0xff = -1, which only reads as a signed
    left-side bearing. Rendering "Bookshelf" from `system_medium.xtf` with that order reproduces the
    stock's Home title in `tests/data/stock-home-panel.png` (rotated ROTATE_270) **pixel for pixel**:
    19 rows, ink columns 2..114 against the panel's 22..134, total advance 115 px.

`build` derives everything it can from the TTF (cell fit, per-glyph metrics, the advance table, the
ranges from the TTF's own cmap intersected with the card font's coverage) and **copies** the header
fields above that have no known meaning, plus the per-cell baseline the card font uses (17 for cell
20, 20 for cell 24). Those are the constants in `CELL_PROFILE`; a cell size that is not 20 or 24 is
refused rather than guessed. No `.hot.xtfp` is written: `required_features` drops `xfp3-hot-v3.3`
and the stock generates its own `.base.xtfp` cache on the card.

`Xtf.from_bytes(data)`, `Xtf.glyph(cp)`, `Xtf.render(text)`, `build_xtf`, `build_package` and
`install_package` are importable; `tests/test_xtfont.py` is the check.
"""
import argparse, hashlib, io, json, os, shutil, struct, subprocess, sys, zipfile, zlib

MAGIC = b'XTF0'
HEADER = 0x40
VERSION = 2
RANGE_ENTRY = 16
ASCII_FIRST, ASCII_COUNT = 0x20, 95        # the advance table's range, and range-table entry 0
PART_OFFSET = 1048576                      # the card's FAT partition starts at 1 MiB (mksd.py)

# Per-cell header values that could not be derived from a TTF: read out of the card's
# misans-demibold pair and written verbatim. `advance_y` and `byte0d` are derivable (cell - 2,
# cell) and kept here only to keep one table per cell.
CELL_PROFILE = {
    20: {'unk06': 0x140, 'unk08': 3, 'advance_y': 18, 'byte0d': 20, 'unk0e': 11, 'unk0f': 2,
         'line_height': 21, 'descent': -6, 'baseline': 17},
    24: {'unk06': 0x140, 'unk08': 3, 'advance_y': 22, 'byte0d': 24, 'unk0e': 13, 'unk0f': 2,
         'line_height': 26, 'descent': -7, 'baseline': 20},
}
ROLES = (('system_small', 'system_small.xtf', 20), ('system_body', 'system_medium.xtf', 24))

# What a package must cover. The card font's own ranges are added on top of these when a reference
# `.xtf` is given (`--like`), so a CJK TTF reaches the same codepoints the stock font does.
BASE_RANGES = ((0x20, 0x7e), (0xa0, 0xff), (0x100, 0x17f), (0x180, 0x24f),
               (0x2b0, 0x2ff), (0x370, 0x3ff), (0x400, 0x4ff), (0x2000, 0x206f),
               (0x20a0, 0x20bf), (0x2100, 0x214f), (0x2190, 0x21ff), (0x2200, 0x22ff),
               (0x2500, 0x257f), (0x25a0, 0x25ff), (0x2600, 0x26ff), (0xfb00, 0xfb06))


class XtfError(ValueError):
    """An `.xtf` that cannot be parsed (bad magic, impossible header, short file)."""


def _s8(v):
    return v - 256 if v > 127 else v


class Glyph:
    """One glyph record: the four metric bytes and the cell bitmap (rows padded to bytes, MSB
    first, 1 = ink)."""

    __slots__ = ('codepoint', 'index', 'advance_x', 'advance_y', 'x_off', 'y_off', 'bitmap',
                 'cell_w', 'cell_h')

    def __init__(self, codepoint, index, record, cell_w, cell_h):
        self.codepoint, self.index = codepoint, index
        self.advance_x, self.advance_y = record[0], record[1]
        self.x_off, self.y_off = _s8(record[2]), _s8(record[3])
        self.bitmap, self.cell_w, self.cell_h = record[4:], cell_w, cell_h

    @property
    def stride(self):
        return (self.cell_w + 7) // 8

    def pixels(self):
        """[(x, y)] of the ink pixels inside the cell (before x_off/y_off are applied)."""
        out, stride = [], self.stride
        for y in range(self.cell_h):
            row = self.bitmap[y * stride:(y + 1) * stride]
            for x in range(self.cell_w):
                if row[x >> 3] >> (7 - (x & 7)) & 1:
                    out.append((x, y))
        return out

    def __repr__(self):
        return (f'<Glyph U+{self.codepoint:04X} #{self.index} adv={self.advance_x} '
                f'off=({self.x_off},{self.y_off})>')


class Xtf:
    """A parsed `.xtf` v2 font. `from_bytes` validates every offset against the file length."""

    def __init__(self, data):
        self.data = bytes(data)
        d = self.data
        if len(d) < HEADER:
            raise XtfError(f'not an .xtf: {len(d)} bytes, shorter than the {HEADER}-byte header')
        if d[:4] != MAGIC:
            raise XtfError(f'bad magic {bytes(d[:4])!r}, expected {MAGIC!r}')
        (self.version, self.unk06, self.unk08) = struct.unpack_from('<HHH', d, 4)
        if self.version != VERSION:
            raise XtfError(f'unsupported .xtf version {self.version} (only v{VERSION} is known)')
        self.cell_w, self.cell_h, self.advance_y, self.byte0d, self.unk0e, self.unk0f = d[0x0a:0x10]
        self.line_height, self.descent = struct.unpack_from('<hh', d, 0x10)
        (self.range_count, self.glyph_count, self.range_offset, self.unk20, self.glyph_offset,
         self.record_size, self.glyph_bytes) = struct.unpack_from('<7I', d, 0x14)
        self.hash8 = d[0x30:0x38]
        self.advance_offset, self.advance_count = struct.unpack_from('<II', d, 0x38)
        if not self.cell_w or not self.cell_h:
            raise XtfError(f'bad cell {self.cell_w}x{self.cell_h}')
        want = 4 + self.cell_h * ((self.cell_w + 7) // 8)
        if self.record_size < want:
            raise XtfError(f'glyph record size {self.record_size} is too small for a '
                           f'{self.cell_w}x{self.cell_h} cell ({want} bytes needed)')
        end = self.glyph_offset + self.glyph_count * self.record_size
        if end > len(d):
            raise XtfError(f'truncated: {self.glyph_count} records of {self.record_size} bytes at '
                           f'{self.glyph_offset:#x} need {end} bytes, file holds {len(d)}')
        if self.range_offset + self.range_count * RANGE_ENTRY > len(d):
            raise XtfError(f'range table ({self.range_count} entries at {self.range_offset:#x}) '
                           f'runs past the end of the file')
        self.ranges = [struct.unpack_from('<IIIHH', d, self.range_offset + i * RANGE_ENTRY)
                       for i in range(self.range_count)]
        self.advances = d[self.advance_offset:self.advance_offset + self.advance_count]

    @classmethod
    def from_bytes(cls, data):
        return cls(data)

    @classmethod
    def open(cls, path):
        with open(path, 'rb') as f:
            return cls(f.read())

    # ------------------------------------------------------------------ lookup
    def glyph_index(self, cp):
        """The glyph index of a codepoint, or None when no range covers it."""
        lo, hi = 0, len(self.ranges) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            first, count, first_index = self.ranges[mid][:3]
            if cp < first:
                hi = mid - 1
            elif cp >= first + count:
                lo = mid + 1
            else:
                return first_index + cp - first
        return None

    def glyph(self, cp):
        """The Glyph for a codepoint, or None when the font does not cover it."""
        i = self.glyph_index(cp)
        if i is None or i >= self.glyph_count:
            return None
        o = self.glyph_offset + i * self.record_size
        return Glyph(cp, i, self.data[o:o + self.record_size], self.cell_w, self.cell_h)

    def codepoints(self):
        for first, count, *_ in self.ranges:
            yield from range(first, first + count)

    def header_info(self):
        """The header as a JSON-able dict, field names as in the module docstring."""
        return {'magic': MAGIC.decode(), 'version': self.version, 'unk06': self.unk06,
                'unk08': self.unk08, 'cell_w': self.cell_w, 'cell_h': self.cell_h,
                'advance_y': self.advance_y, 'byte0d': self.byte0d, 'unk0e': self.unk0e,
                'unk0f': self.unk0f, 'line_height': self.line_height, 'descent': self.descent,
                'range_count': self.range_count, 'glyph_count': self.glyph_count,
                'range_offset': self.range_offset, 'unk20': self.unk20,
                'glyph_offset': self.glyph_offset, 'record_size': self.record_size,
                'glyph_bytes': self.glyph_bytes, 'hash8': self.hash8.hex(),
                'advance_offset': self.advance_offset, 'advance_count': self.advance_count,
                'file_bytes': len(self.data)}

    # ------------------------------------------------------------------ rendering
    def layout(self, text):
        """[(glyph, pen_x)] plus the total advance, walking the per-glyph `advance_x`."""
        placed, pen = [], 0
        for ch in text:
            g = self.glyph(ord(ch))
            if g is not None:
                placed.append((g, pen))
                pen += g.advance_x
            else:
                pen += self.advance_y // 2                      # missing: a blank of half a line
        return placed, pen

    def render(self, text, scale=1, pad=2):
        """A PIL image ('1', 1 = white paper) of `text` laid out with the font's own metrics, and a
        dict with the total advance and the ink bounding box. The pen starts at (pad, pad) and the
        cell top of every glyph sits on the pen's row, exactly as the stock draws a line."""
        from PIL import Image
        placed, total = self.layout(text)
        height = pad * 2 + self.line_height + self.cell_h
        im = Image.new('1', (max(1, total + pad * 2), height), 1)
        px = im.load()
        bbox = None
        for g, pen in placed:
            for x, y in g.pixels():
                X, Y = pad + pen + g.x_off + x, pad + g.y_off + y
                if 0 <= X < im.width and 0 <= Y < im.height:
                    px[X, Y] = 0
                    bbox = (X, Y, X, Y) if bbox is None else (min(bbox[0], X), min(bbox[1], Y),
                                                              max(bbox[2], X), max(bbox[3], Y))
        if scale > 1:
            im = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
        return im, {'text': text, 'glyphs': len(placed), 'advance': total, 'ink_bbox': bbox,
                    'pad': pad, 'baseline_guess': pad + self.cell_h}

    def render_glyph(self, cp, scale=1):
        """A PIL image of one glyph's cell (no offsets applied) and its metrics."""
        from PIL import Image
        g = self.glyph(cp)
        if g is None:
            raise XtfError(f'U+{cp:04X} is not in this font')
        im = Image.new('1', (self.cell_w, self.cell_h), 1)
        px = im.load()
        for x, y in g.pixels():
            px[x, y] = 0
        if scale > 1:
            im = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
        return im, {'codepoint': cp, 'index': g.index, 'advance_x': g.advance_x,
                    'advance_y': g.advance_y, 'x_off': g.x_off, 'y_off': g.y_off}


# ---------------------------------------------------------------- TTF coverage
def ttf_codepoints(path):
    """The codepoints a TTF's `cmap` maps, from formats 4 and 12 (a ~60-line parser so the tool
    needs no fontTools; Pillow's ImageFont cannot be asked what a font covers)."""
    with open(path, 'rb') as f:
        data = f.read()
    tag = data[:4]
    if tag == b'ttcf':
        off = struct.unpack_from('>I', data, 12)[0]                 # first font of a collection
    else:
        off = 0
    num_tables = struct.unpack_from('>H', data, off + 4)[0]
    cmap = None
    for i in range(num_tables):
        rec = off + 12 + i * 16
        if data[rec:rec + 4] == b'cmap':
            cmap = struct.unpack_from('>I', data, rec + 8)[0]
            break
    if cmap is None:
        raise ValueError(f'{path}: no cmap table')
    n = struct.unpack_from('>H', data, cmap + 2)[0]
    best, best_score = None, -1
    for i in range(n):
        pid, eid, sub = struct.unpack_from('>HHI', data, cmap + 4 + i * 8)
        fmt = struct.unpack_from('>H', data, cmap + sub)[0]
        score = {(3, 10): 5, (0, 4): 4, (0, 6): 4, (3, 1): 3, (0, 3): 3, (0, 1): 2}.get((pid, eid), 0)
        if fmt in (4, 12) and score > best_score:
            best, best_score = (cmap + sub, fmt), score
    if best is None:
        raise ValueError(f'{path}: no cmap subtable in format 4 or 12')
    sub, fmt = best
    out = set()
    if fmt == 4:
        seg2 = struct.unpack_from('>H', data, sub + 6)[0]
        seg = seg2 // 2
        ends = struct.unpack_from(f'>{seg}H', data, sub + 14)
        starts = struct.unpack_from(f'>{seg}H', data, sub + 16 + seg2)
        deltas = struct.unpack_from(f'>{seg}h', data, sub + 16 + seg2 * 2)
        range_off_at = sub + 16 + seg2 * 3
        offs = struct.unpack_from(f'>{seg}H', data, range_off_at)
        for i in range(seg):
            if starts[i] == 0xffff:
                continue
            for cp in range(starts[i], min(ends[i], 0xfffe) + 1):
                if offs[i] == 0:
                    gid = (cp + deltas[i]) & 0xffff
                else:
                    p = range_off_at + i * 2 + offs[i] + (cp - starts[i]) * 2
                    if p + 2 > len(data):
                        continue
                    gid = struct.unpack_from('>H', data, p)[0]
                    if gid:
                        gid = (gid + deltas[i]) & 0xffff
                if gid:
                    out.add(cp)
    else:
        ngroups = struct.unpack_from('>I', data, sub + 12)[0]
        for i in range(ngroups):
            s, e, gid = struct.unpack_from('>III', data, sub + 16 + i * 12)
            if gid and e - s < 0x30000:
                out.update(range(s, e + 1))
    return out


def _merge(codepoints):
    """Sorted codepoints -> [(first, count)] contiguous runs."""
    runs, cps = [], sorted(codepoints)
    for cp in cps:
        if runs and cp == runs[-1][0] + runs[-1][1]:
            runs[-1][1] += 1
        else:
            runs.append([cp, 1])
    return [tuple(r) for r in runs]


def select_codepoints(ttf_path, like=None, extra_ranges=()):
    """The codepoints a package should carry: the TTF's cmap intersected with BASE_RANGES, the
    coverage of a reference `.xtf` (`like`) and any `extra_ranges`, always including U+0020..U+007E
    so the mandatory first range and the 95-entry advance table are complete."""
    have = ttf_codepoints(ttf_path)
    want = set()
    for first, last in tuple(BASE_RANGES) + tuple(extra_ranges):
        want.update(range(first, last + 1))
    if like is not None:
        want.update(like.codepoints())
    keep = {cp for cp in want & have if cp >= 0x20}
    keep.update(range(ASCII_FIRST, ASCII_FIRST + ASCII_COUNT))     # blank-filled if the TTF lacks one
    return keep


# ---------------------------------------------------------------- building
def fit_size(ttf_path, cell, start=None):
    """The largest pixel size whose ascent+descent fits the cell's line box (the card font's line
    height, one to two pixels over the cell) and whose 'M' advance fits the cell width."""
    from PIL import ImageFont
    prof = CELL_PROFILE[cell]
    for size in range(start or cell + 6, 5, -1):
        font = ImageFont.truetype(ttf_path, size)
        asc, desc = font.getmetrics()
        if asc + desc <= prof['line_height'] and asc <= prof['baseline'] \
                and round(font.getlength('M')) <= cell:
            return size
    raise ValueError(f'no size of {ttf_path} fits a {cell}x{cell} cell')


def _rasterise(font, cell_w, cell_h, baseline, cp):
    """(advance_x, x_off, y_off, bitmap bytes) for one codepoint, drawn without antialiasing with
    the pen at the cell's top-left corner and the baseline `baseline` rows below it."""
    from PIL import Image, ImageDraw
    pad = cell_w
    im = Image.new('L', (cell_w * 3, cell_h * 3), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = '1'                                        # FreeType monochrome: no antialiasing
    d.text((pad, pad + baseline), chr(cp), font=font, fill=255, anchor='ls')
    im = im.point(lambda v: 255 if v >= 128 else 0)
    box = im.getbbox()
    advance = max(0, min(cell_w, round(font.getlength(chr(cp)))))
    stride = (cell_w + 7) // 8
    if box is None:
        return advance, 0, 0, bytes(stride * cell_h)
    x_off, y_off = box[0] - pad, box[1] - pad
    w, h = min(box[2] - box[0], cell_w), min(box[3] - box[1], cell_h)
    crop = im.crop((box[0], box[1], box[0] + w, box[1] + h))
    x_off, y_off = max(-128, min(127, x_off)), max(-128, min(127, y_off))
    rows = bytearray(stride * cell_h)
    px = crop.load()
    for y in range(h):
        for x in range(w):
            if px[x, y]:
                rows[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
    return advance, x_off, y_off, bytes(rows)


def build_xtf(ttf_path, cell, codepoints, size=None):
    """The bytes of one `.xtf` v2 file for `ttf_path` at `cell`x`cell`, covering `codepoints`."""
    from PIL import ImageFont
    if cell not in CELL_PROFILE:
        raise ValueError(f'cell {cell} has no measured header profile (only '
                         f'{sorted(CELL_PROFILE)} were read off the card font)')
    prof = CELL_PROFILE[cell]
    size = size or fit_size(ttf_path, cell)
    font = ImageFont.truetype(ttf_path, size)
    runs = _merge(codepoints)
    # entry 0 must be U+0020..U+007E at glyph index 0: the advance table addresses it positionally
    ascii_end = ASCII_FIRST + ASCII_COUNT
    split = []
    for first, count in runs:                       # cut every run around the mandatory ASCII run
        lo, hi = first, first + count
        if lo < ascii_end and hi > ASCII_FIRST:
            if lo < ASCII_FIRST:
                split.append((lo, ASCII_FIRST - lo))
            if hi > ascii_end:
                split.append((ascii_end, hi - ascii_end))
        else:
            split.append((first, count))
    runs = sorted(split + [(ASCII_FIRST, ASCII_COUNT)])

    stride, records, advances = (cell + 7) // 8, [], bytearray(ASCII_COUNT)
    record_size = 4 + stride * cell
    entries, index = [], 0
    for first, count in runs:
        entries.append((first, count, index))
        for cp in range(first, first + count):
            adv, xo, yo, bits = _rasterise(font, cell, cell, prof['baseline'], cp)
            records.append(bytes((adv, prof['advance_y'], xo & 0xff, yo & 0xff)) + bits)
            if ASCII_FIRST <= cp < ASCII_FIRST + ASCII_COUNT:
                advances[cp - ASCII_FIRST] = adv
            index += 1

    range_offset = HEADER
    advance_offset = range_offset + len(entries) * RANGE_ENTRY
    glyph_offset = (advance_offset + ASCII_COUNT + 0x3ff) & ~0x3ff       # the card font pads to 1 KiB
    body = bytearray()
    for first, count, gi in entries:
        body += struct.pack('<IIIHH', first, count, gi, 0, count)
    body += bytes(advances)
    body += bytes(glyph_offset - advance_offset - ASCII_COUNT)
    for r in records:
        body += r
    glyph_bytes = len(records) * record_size
    head = bytearray(HEADER)
    head[0:4] = MAGIC
    struct.pack_into('<HHH', head, 4, VERSION, prof['unk06'], prof['unk08'])
    head[0x0a:0x10] = bytes((cell, cell, prof['advance_y'], prof['byte0d'], prof['unk0e'], prof['unk0f']))
    struct.pack_into('<hh', head, 0x10, prof['line_height'], prof['descent'])
    struct.pack_into('<7I', head, 0x14, len(entries), len(records), range_offset, 0, glyph_offset,
                     record_size, glyph_bytes)
    # The two CRC-32s the stock's header parser (app VA 0x4210db20) checks. 0x30 is the zlib CRC-32
    # of the glyph records alone; 0x34 is the zlib CRC-32 of the 52 header bytes in front of it,
    # compared at 0x4210e553 (the parser memcpy's the header, zeroes 0x30..0x37 and CRC-32s it for
    # its own cache key, so only these two words must be right). Both are little-endian.
    struct.pack_into('<I', head, 0x30, zlib.crc32(bytes(body[glyph_offset - HEADER:])) & 0xffffffff)
    struct.pack_into('<I', head, 0x34, zlib.crc32(bytes(head[:0x34])) & 0xffffffff)
    struct.pack_into('<II', head, 0x38, advance_offset, ASCII_COUNT)
    return bytes(head) + bytes(body), {'size_px': size, 'glyph_count': len(records),
                                       'ranges': len(entries)}


def build_preview(small, medium, display_name, width=320, height=96):
    """The 320x96 mono `.xic` specimen: the display name in the medium font, then a sample line in
    each of the two fonts, drawn with the fonts themselves (`tools/xic2png.encode_xic`)."""
    from PIL import Image
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import xic2png
    canvas = Image.new('1', (width, height), 1)
    lines = [(medium, display_name[:26], 2), (small, 'Small  Aa Bb 123', 32),
             (medium, 'Medium  Aa Bb 123', 56)]
    for font, text, y in lines:
        im, _ = font.render(text, pad=0)
        canvas.paste(im.crop((0, 0, min(im.width, width - 8), min(im.height, height - y))), (6, y))
    return xic2png.encode_xic(canvas), canvas


def build_package(ttf_path, font_id, display_name, out_dir, small_size=None, body_size=None,
                  like=None, family=None, style='Regular', revision=1, version_name='1.0.0',
                  license_name='', description=''):
    """Write `<out_dir>/system_small.xtf`, `system_medium.xtf`, `manifest.json`, `preview.xic` and
    `<out_dir>/<font_id>-r<revision>.xtfont`. Returns the manifest dict plus a report."""
    os.makedirs(out_dir, exist_ok=True)
    ref = Xtf.open(like) if like else None
    codepoints = select_codepoints(ttf_path, ref)
    manifest = {
        'created_by': {'tool': 'xtfont.py', 'version': '1.0.0'},
        'description': description or f'1bpp system font built from {os.path.basename(ttf_path)}',
        'display_name': display_name, 'family': family or display_name, 'font_id': font_id,
        'format': 'xteink-system-font-package', 'format_version': 3, 'license_name': license_name,
        'package_revision': revision,
        'required_features': ['xtf-v2', 'system-family-1bpp'],       # no hot cache: no xfp3-hot-v3.3
        'roles': {}, 'style': style, 'vendor': '', 'version_name': version_name,
    }
    report, fonts = {}, {}
    for role, filename, cell in ROLES:
        blob, info = build_xtf(ttf_path, cell, codepoints,
                              small_size if cell == 20 else body_size)
        with open(os.path.join(out_dir, filename), 'wb') as f:
            f.write(blob)
        fonts[cell] = Xtf.from_bytes(blob)
        manifest['roles'][role] = {'font': {
            'advance_y': CELL_PROFILE[cell]['advance_y'], 'bpp': 1, 'cell_height': cell,
            'cell_width': cell, 'format_version': '2.0', 'glyph_count': info['glyph_count'],
            'path': f'assets/{filename}', 'sha256': hashlib.sha256(blob).hexdigest(),
            'size': len(blob)}}
        report[role] = dict(info, file=filename, bytes=len(blob))
    preview, _ = build_preview(fonts[20], fonts[24], display_name)
    with open(os.path.join(out_dir, 'preview.xic'), 'wb') as f:
        f.write(preview)
    manifest['preview'] = {'bpp': 1, 'height': 96, 'path': 'assets/preview.xic',
                           'sha256': hashlib.sha256(preview).hexdigest(), 'size': len(preview),
                           'width': 320}
    blob = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    with open(os.path.join(out_dir, 'manifest.json'), 'wb') as f:
        f.write(blob)
    pkg = os.path.join(out_dir, f'{font_id}-r{revision}.xtfont')
    members = [('manifest.json', blob)] + \
              [(f'assets/{fn}', open(os.path.join(out_dir, fn), 'rb').read())
               for _, fn, _ in ROLES] + \
              [('assets/preview.xic', preview)]
    with zipfile.ZipFile(pkg, 'w', zipfile.ZIP_STORED) as z:        # the card's package is stored
        for name, payload in members:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.external_attr = 0o100644 << 16
            z.writestr(zi, payload)
    report['package'] = pkg
    report['manifest'] = os.path.join(out_dir, 'manifest.json')
    report['codepoints'] = len(codepoints)
    return manifest, report


# ---------------------------------------------------------------- installing
def _mtools(img, *args, check=True):
    return subprocess.run([args[0], '-i', f'{img}@@{PART_OFFSET}', *args[1:]],
                          capture_output=True, text=True, check=check)


def install_package(img, package, direct=False, font_id=None):
    """Put `package` in `XTCache/system_font_downloads/` on a card image (the stock's own installer
    picks it up from Settings -> System Font). With `direct=True` also unpack it into
    `XTData/system_fonts/<font_id>/` and rewrite `selection.config`, so the font is active at the
    next boot without touching the UI. Returns a dict of what was written."""
    out = {'image': img, 'package': os.path.basename(package)}
    _mtools(img, 'mmd', '::/XTCache', check=False)
    _mtools(img, 'mmd', '::/XTCache/system_font_downloads', check=False)
    _mtools(img, 'mcopy', '-o', package, '::/XTCache/system_font_downloads/')
    out['downloaded_to'] = '/XTCache/system_font_downloads/' + os.path.basename(package)
    if not direct:
        return out
    with zipfile.ZipFile(package) as z:
        manifest = json.loads(z.read('manifest.json'))
        fid = font_id or manifest['font_id']
        tmp = os.path.join(os.path.dirname(os.path.abspath(package)), f'.unpack-{fid}')
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        names = []
        for name in z.namelist():
            base = os.path.basename(name)                # assets/X -> X in the installed directory
            with open(os.path.join(tmp, base), 'wb') as f:
                f.write(z.read(name))
            names.append(base)
    _mtools(img, 'mmd', '::/XTData', check=False)
    _mtools(img, 'mmd', '::/XTData/system_fonts', check=False)
    _mtools(img, 'mmd', f'::/XTData/system_fonts/{fid}', check=False)
    _mtools(img, 'mcopy', '-o', *[os.path.join(tmp, n) for n in names],
            f'::/XTData/system_fonts/{fid}/')
    roles = manifest['roles']
    cfg = ('format_version=1\nmode=external\n'
           f'font_id={fid}\n'
           f"small_asset_id={roles['system_small']['font']['sha256']}\n"
           f"body_asset_id={roles['system_body']['font']['sha256']}\n")
    cfgfile = os.path.join(tmp, 'selection.config')
    with open(cfgfile, 'w') as f:
        f.write(cfg)
    _mtools(img, 'mcopy', '-o', cfgfile, '::/XTData/system_fonts/selection.config')
    shutil.rmtree(tmp, ignore_errors=True)
    out['installed_to'] = f'/XTData/system_fonts/{fid}/'
    out['selection'] = cfg
    out['font_id'] = fid
    return out


# ---------------------------------------------------------------- CLI
def _codepoint(text):
    if len(text) == 1 and not text.isdigit():
        return ord(text)
    return int(text, 0)


def cmd_dump(a):
    font = Xtf.open(a.src)
    if a.info:
        print(json.dumps(font.header_info(), indent=2))
    if a.glyph is not None:
        im, info = font.render_glyph(_codepoint(a.glyph), a.scale)
    else:
        text = a.text if a.text is not None else 'Bookshelf gjpqy 0123'
        im, info = font.render(text, a.scale)
    if a.dst:
        im.save(a.dst)
    if not a.info:
        print(json.dumps(info))
    return 0


def cmd_build(a):
    manifest, report = build_package(a.ttf, a.id, a.name, a.out_dir, a.small_size, a.body_size,
                                     like=a.like, family=a.family, style=a.style,
                                     revision=a.revision, license_name=a.license_name)
    print(json.dumps(report, indent=2))
    return 0


def cmd_install(a):
    print(json.dumps(install_package(a.image, a.package, a.direct, a.id), indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    d = sub.add_parser('dump', help='parse an .xtf and render glyphs or a string to a PNG')
    d.add_argument('src')
    d.add_argument('dst', nargs='?', help='the PNG to write')
    d.add_argument('--glyph', help='one codepoint (65, 0x41 or A) instead of a string')
    d.add_argument('--text', help='the string to lay out with the font\'s advance metrics')
    d.add_argument('--scale', type=int, default=1, help='nearest-neighbour magnification')
    d.add_argument('--info', action='store_true', help='print the header fields as JSON')
    d.set_defaults(func=cmd_dump)

    b = sub.add_parser('build', help='rasterise a TTF into an installable .xtfont package')
    b.add_argument('ttf')
    b.add_argument('out_dir')
    b.add_argument('--id', required=True, help='font_id (the directory name under system_fonts/)')
    b.add_argument('--name', required=True, help='display name shown in Settings -> System Font')
    b.add_argument('--small-size', type=int, help='pixel size for the 20x20 cell (default: fitted)')
    b.add_argument('--body-size', type=int, help='pixel size for the 24x24 cell (default: fitted)')
    b.add_argument('--like', help='an .xtf whose coverage is added to the target codepoint set')
    b.add_argument('--family', help='family name for the manifest (default: the display name)')
    b.add_argument('--style', default='Regular')
    b.add_argument('--revision', type=int, default=1)
    b.add_argument('--license-name', default='')
    b.set_defaults(func=cmd_build)

    i = sub.add_parser('install', help='put a package on a card image')
    i.add_argument('image')
    i.add_argument('package')
    i.add_argument('--direct', action='store_true',
                   help='also unpack into XTData/system_fonts/<id>/ and rewrite selection.config')
    i.add_argument('--id', help='font_id to install as (default: the manifest\'s)')
    i.set_defaults(func=cmd_install)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (XtfError, ValueError) as e:
        sys.exit(f'{a.cmd}: {e}')


if __name__ == '__main__':
    sys.exit(main())
