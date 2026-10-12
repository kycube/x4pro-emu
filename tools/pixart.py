"""tools/pixart.py -- a small 1-bit pixel-art kit for the stock firmware's Lua apps.

Paint tones (0 black .. 255 white) at logical resolution, quantise through a 4x4 Bayer matrix into the
classic 1-bit patterns (checker, dots, stripes), then ink crisp black and white detail on top. What
works on this panel: large solid masses, patterns only where light falls off, strong silhouettes;
a scene that is patterned everywhere reads as noise. `tools/gamebook.py` turns a Scene into the run
data its Lua engine paints (stories/gull-rock/scenes.py is the worked example).
"""
from PIL import Image, ImageDraw

BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


class Scene:
    def __init__(self, w=160, h=100, tone=255):
        self.w, self.h = w, h
        self.tone = Image.new('L', (w, h), tone)     # the painted tones
        self.t = ImageDraw.Draw(self.tone)
        self.ink = None                              # 1-bit layer after quantise()

    # ---- tone painting (before quantise) ----
    def vgrad(self, y0, y1, t0, t1, x0=0, x1=None):
        x1 = self.w if x1 is None else x1
        for y in range(y0, y1):
            f = (y - y0) / max(1, y1 - y0 - 1)
            self.t.line([(x0, y), (x1 - 1, y)], fill=int(t0 + (t1 - t0) * f))

    def poly(self, pts, tone):
        self.t.polygon(pts, fill=tone)

    def rect(self, x0, y0, x1, y1, tone):
        self.t.rectangle([x0, y0, x1, y1], fill=tone)

    def ellipse(self, x0, y0, x1, y1, tone):
        self.t.ellipse([x0, y0, x1, y1], fill=tone)

    def tline(self, pts, tone, width=1):
        self.t.line(pts, fill=tone, width=width)

    def wedge(self, apex, far_a, far_b, t_near, t_far, steps=40):
        """A light beam: a triangle from apex to the segment far_a..far_b, tone fading outwards."""
        ax, ay = apex
        for i in range(steps, 0, -1):
            f = i / steps
            a = (ax + (far_a[0] - ax) * f, ay + (far_a[1] - ay) * f)
            b = (ax + (far_b[0] - ax) * f, ay + (far_b[1] - ay) * f)
            self.t.polygon([apex, a, b], fill=int(t_near + (t_far - t_near) * f))

    def radial(self, cx, cy, rx, ry, t_center, t_edge, steps=24):
        """A pool of light: concentric ellipses from the edge tone in to the centre tone."""
        for i in range(steps, 0, -1):
            f = i / steps
            t = int(t_center + (t_edge - t_center) * f)
            self.t.ellipse([cx - rx * f, cy - ry * f, cx + rx * f, cy + ry * f], fill=t)

    # ---- quantise, then ink ----
    def quantise(self):
        src = self.tone.load()
        self.ink = Image.new('1', (self.w, self.h), 1)
        dst = self.ink.load()
        for y in range(self.h):
            for x in range(self.w):
                thr = (BAYER4[y % 4][x % 4] + 0.5) * 16
                dst[x, y] = 1 if src[x, y] > thr else 0
        self.d = ImageDraw.Draw(self.ink)
        return self

    def pattern(self, pts, tone, kind='poly'):
        """Fill a shape with the Bayer pattern of `tone`, after quantise (0 = black, 255 = white)."""
        mask = Image.new('1', (self.w, self.h), 0)
        md = ImageDraw.Draw(mask)
        if kind == 'poly':
            md.polygon(pts, fill=1)
        elif kind == 'ellipse':
            md.ellipse(pts, fill=1)
        else:
            md.rectangle(pts, fill=1)
        m, dst = mask.load(), self.ink.load()
        for y in range(self.h):
            for x in range(self.w):
                if m[x, y]:
                    dst[x, y] = 1 if tone > (BAYER4[y % 4][x % 4] + 0.5) * 16 else 0

    def black(self, pts, kind='poly', width=1):
        self._ink(pts, kind, 0, width)

    def white(self, pts, kind='poly', width=1):
        self._ink(pts, kind, 1, width)

    def _ink(self, pts, kind, v, width):
        if kind in ('rect', 'ellipse'):
            (x0, y0), (x1, y1) = pts
            pts = [(min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1))]
        if kind == 'poly':
            self.d.polygon(pts, fill=v)
        elif kind == 'line':
            self.d.line(pts, fill=v, width=width)
        elif kind == 'rect':
            self.d.rectangle(pts, fill=v)
        elif kind == 'ellipse':
            self.d.ellipse(pts, fill=v)
        elif kind == 'point':
            self.d.point(pts, fill=v)

    def save(self, path, scale=3):
        self.ink.save(path.replace('.png', '-1x.png'))
        self.ink.resize((self.w * scale, self.h * scale), Image.NEAREST).save(path)


def runs(img):
    """Black runs per row: what the reader draws as one g:line each."""
    w, h = img.size
    p = img.load()
    n = 0
    for y in range(h):
        prev = 1
        for x in range(w):
            v = p[x, y]
            if v == 0 and prev != 0:
                n += 1
            prev = v
    return n
