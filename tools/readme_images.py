#!/usr/bin/env python3
"""Rebuild the README's pictures (docs/images/*.png) from the test goldens and the gamebook's art.

    .venv/bin/python tools/readme_images.py

Every picture is something a test already pins, so the README shows what the emulator really draws:
  gallery.png    six screens: the stock 7.x Home, its menu, its reader, a Lua status screen, and the
                 Gull Rock gamebook's cover and a plate page (tests/golden/stock-*, gamebook-*)
  fidelity.png   CrossPoint's Home as the real reader screenshots itself next to the emulator's
                 panel; test_device_screenshot.py sets the battery to the device's and holds the two
                 at 0 differing pixels (the golden shown here is at the default 63 %)
  plates.png     the gamebook's eight plates, as tools/gamebook.py renders them
Run it after a golden or a plate changes; captions use Inter (fonts/inter/, OFL, fetched locally per
fonts/README.md) and fall back to PIL's default font.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, 'tests', 'golden')
OUT = os.path.join(ROOT, 'docs', 'images')
sys.path.insert(0, os.path.join(ROOT, 'tools'))

BG, INK, MUTED = 255, 0, 110


def font(size, weight=500):
    path = os.path.join(ROOT, 'fonts', 'inter', 'Inter-variable.ttf')
    try:
        f = ImageFont.truetype(path, size)
        try:
            f.set_variation_by_axes([weight])
        except (OSError, AttributeError):
            pass
        return f
    except OSError:
        return ImageFont.load_default()


def upright(name):
    """A panel golden (800 x 480 landscape, the portrait UI drawn rotated) as the reader shows it."""
    im = Image.open(os.path.join(GOLD, name)).convert('L')
    return im.rotate(-90, expand=True) if im.width > im.height else im


def framed(im, pad=10):
    """The screen with a thin bezel line, so white pages read as pages on a white README."""
    out = Image.new('L', (im.width + 2 * pad, im.height + 2 * pad), BG)
    out.paste(im, (pad, pad))
    d = ImageDraw.Draw(out)
    d.rectangle([pad - 1, pad - 1, pad + im.width, pad + im.height], outline=170, width=1)
    return out


def grid(cells, cols, gap=28, cap=38):
    """cells: [(image, caption)] -> one picture, captions centred under each screen."""
    w = max(c[0].width for c in cells)
    h = max(c[0].height for c in cells)
    rows = (len(cells) + cols - 1) // cols
    out = Image.new('L', (cols * w + (cols - 1) * gap, rows * (h + cap) + (rows - 1) * gap), BG)
    d = ImageDraw.Draw(out)
    f = font(19)
    for i, (im, caption) in enumerate(cells):
        x = (i % cols) * (w + gap)
        y = (i // cols) * (h + cap + gap)
        out.paste(im, (x + (w - im.width) // 2, y))
        tw = d.textlength(caption, font=f)
        d.text((x + (w - tw) / 2, y + h + 8), caption, font=f, fill=MUTED)
    return out


def gallery():
    cells = [(framed(upright('stock-home.png')), 'Stock firmware: Home'),
             (framed(upright('stock-menu.png')), 'Stock firmware: the menu'),
             (framed(upright('stock-reader-page1.png')), 'Stock firmware: reading'),
             (framed(upright('stock-lua-status-panels.png')), 'A Lua app: status screen'),
             (framed(upright('gamebook-title.png')), 'A Lua app: the Gull Rock gamebook'),
             (framed(upright('gamebook-ledge.png')), 'Gull Rock: a plate page')]
    return grid(cells, 3)


def fidelity():
    dev = Image.open(os.path.join(GOLD, 'device-home-screenshot-3824.bmp')).convert('L')
    emu = upright('home.png')
    return grid([(framed(dev), 'The reader\'s own screenshot (CrossPoint)'),
                 (framed(emu), 'The emulator: 0 px apart once the battery matches')], 2)


def plates():
    import gamebook
    story_dir = os.path.join(ROOT, 'stories', 'gull-rock')
    story = gamebook.parse_story(open(os.path.join(story_dir, 'story.txt')).read())
    art, _ = gamebook.build_art(story, gamebook.load_scenes(story_dir))
    order = list(gamebook.load_scenes(story_dir))                       # story order, not A-Z
    names = [n for n in order if n in art]
    cells = []
    for n in names:
        img, s = art[n]
        cells.append((img.resize((img.width * s, img.height * s), Image.NEAREST).convert('L'), n))
    return grid(cells, 4, gap=16, cap=34)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, make in (('gallery.png', gallery), ('fidelity.png', fidelity), ('plates.png', plates)):
        im = make()
        path = os.path.join(OUT, name)
        im.save(path, optimize=True)
        print(f'{path}: {im.width} x {im.height}')


if __name__ == '__main__':
    main()
