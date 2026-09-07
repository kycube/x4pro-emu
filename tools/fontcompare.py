#!/usr/bin/env python3
"""Render candidate TTFs through the device's own rasteriser and stack them for a by-eye choice.

    tools/fontcompare.py OUT.png  "Name=path/to/font.ttf[:SMALL,BODY]" ...   [--current]
    tools/fontcompare.py OUT.png  --current                      # just what the device runs now

Choosing a system font from a specimen on a Mac is misleading: the stock has no FreeType and draws
every glyph as a **1 bpp bitmap** out of a fixed 20x20 or 24x24 cell (docs/fonts.md, tools/xtfont.py).
A face that is elegant at 40 px anti-aliased can be a smudge at 17 ppem with no grey. So each
candidate here is built into the two cells with `xtfont.build_package` -- the same code path that
makes a real system font -- and then drawn with that font's own advance metrics, magnified so every
pixel decision is visible. What you see is what the panel would show.

`Name=font.ttf:17,20` forces the small and body pixel sizes; without them `fit_size` chooses, and it
chooses from the font's *declared* vertical metrics, which under-sizes roomy faces badly
(docs/fonts.md §0). `--current` prepends the device's own font, read from the card mirror, as the
baseline to beat: a candidate that looks thinner than that row will look thinner on the device.

Nothing is written anywhere but OUT.png; the built packages go to a temporary directory.
"""
import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import xtfont                                                            # noqa: E402

CARD = os.path.join(ROOT, 'images', 'device', 'sd-files', 'XTData', 'system_fonts', 'misans-demibold')
BODY_LINE = 'Hamburgefonstiv  The quick brown fox'
SMALL_LINE = 'Settings  Bookshelf  63%  12:04  All Files'


def faces(ttf, small, body, fid):
    """(small 20-cell face, body 24-cell face) for a TTF, or for the device's current font."""
    if ttf is None:
        return (xtfont.Xtf.open(os.path.join(CARD, 'system_small.xtf')),
                xtfont.Xtf.open(os.path.join(CARD, 'system_medium.xtf')))
    out = tempfile.mkdtemp(prefix='fontcompare-')
    kw = {}
    if small:
        kw['small_size'] = small
    if body:
        kw['body_size'] = body
    xtfont.build_package(ttf, fid, fid, out, style='Regular', **kw)
    return (xtfont.Xtf.open(os.path.join(out, 'system_small.xtf')),
            xtfont.Xtf.open(os.path.join(out, 'system_medium.xtf')))


def parse(spec):
    """"Name=path.ttf:17,20" -> (name, path, small, body); the sizes are optional."""
    name, _, rest = spec.partition('=')
    if not rest:
        raise SystemExit(f'bad candidate {spec!r}: expected Name=path.ttf[:SMALL,BODY]')
    path, _, sizes = rest.partition(':')
    small = body = None
    if sizes:
        parts = sizes.split(',')
        if len(parts) != 2:
            raise SystemExit(f'bad sizes in {spec!r}: expected :SMALL,BODY')
        small, body = int(parts[0]), int(parts[1])
    return name, path, small, body


def sheet(dst, candidates, scale):
    from PIL import Image, ImageDraw
    rows = []
    for name, path, small, body in candidates:
        try:
            sm, bd = faces(path, small, body, (name.split() or ['f'])[0].lower())
            rows.append((name, bd.render(BODY_LINE, pad=0)[0], sm.render(SMALL_LINE, pad=0)[0], None))
        except Exception as e:                    # a face the builder refuses is a result, not a crash
            rows.append((name, None, None, f'{type(e).__name__}: {e}'))
    pad, gap, label = 20, 8, 24
    width = max((max(a.width, b.width) * scale for _, a, b, _ in rows if a), default=800) + pad * 2
    height = pad + sum((label + (a.height + b.height) * scale + gap * 3) if a else (label + gap * 2)
                       for _, a, b, _ in rows)
    im = Image.new('L', (width, height), 255)
    d = ImageDraw.Draw(im)
    y = pad
    for name, a, b, err in rows:
        d.text((pad, y + 5), name if not err else f'{name}  --  {err}', fill=0)
        y += label
        if a:
            for g in (a, b):
                big = g.convert('L').resize((g.width * scale, g.height * scale), Image.NEAREST)
                im.paste(big, (pad, y))
                y += big.height + gap
        y += gap
        d.line([pad, y - 4, width - pad, y - 4], fill=210)
    im.save(dst)
    print(f'{dst}: {im.size[0]}x{im.size[1]}, {len(rows)} row(s), {scale}x')
    for name, a, _, err in rows:
        if err:
            print(f'  {name}: {err}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('out', help='the PNG to write')
    ap.add_argument('candidates', nargs='*', help='Name=path.ttf[:SMALL,BODY]')
    ap.add_argument('--current', action='store_true',
                    help="prepend the device's own font from images/device/sd-files as the baseline")
    ap.add_argument('--scale', type=int, default=3, help='nearest-neighbour magnification (default 3)')
    a = ap.parse_args()
    cands = []
    if a.current:
        if not os.path.isdir(CARD):
            raise SystemExit(f'no card mirror at {CARD}: --current needs images/device/sd-files')
        cands.append(('MiSans Demibold  (the device today)', None, None, None))
    cands += [parse(c) for c in a.candidates]
    if not cands:
        raise SystemExit('nothing to render: give at least one candidate, or --current')
    sheet(a.out, cands, a.scale)


if __name__ == '__main__':
    main()
