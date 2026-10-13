#!/usr/bin/env python3
r"""Device parity for the stock firmware: one step script, walked on the emulator by x4emu and on the
owner's reader by hand, and the stock's own Screen Captures of both compared pixel for pixel.

  parity.py emu SCRIPT [--session DIR] [--dump F] [--name N]    walk SCRIPT in the emulator
  parity.py runbook SCRIPT [--out FILE.md]                      the owner's numbered checklist
  parity.py prepare-card SCRIPT MOUNT [--session DIR] [--yes]   check (and with --yes: prepare) the
                                                                reader's card mounted in USB mode
  parity.py collect SCRIPT MOUNT [--session DIR]                copy the session's captures off it
  parity.py compare SCRIPT DEVICE_XIC_DIR EMU_DIR [--out DIR] [--precheck F] [--not-met NAME ...]
  parity.py book OUT.epub                                       the parity book, byte for byte

There is no remote input path into the reader, so the device side is "guided": the owner follows the
runbook, and the only device pixels there are come from the stock's own **Screen Capture** (light
panel -> Screen Capture, `/sdcard/screenshots/screenshot_YYYYMMDD_HHMMSS.xic`), which exists only in an
image patched with `tools/stockpatch.py apply IMAGE developer-menu` (docs/xic.md). The emulator side
captures the same way, so the comparison is between two files the same firmware wrote: device .xic
against emulator .xic, both in the upright portrait frame. `emu` also checks that every emulator .xic
equals the panel screenshot of the same screen, which is what ties the panel model to the captures.

**The step script** (JSON, `parity/*.json`; `parity/stock-7.6.8.json` is the first) names the
screens in walking order. Everything is in the portrait frame the UI is drawn in, 480 wide and 800
tall, origin top left; x4emu's landscape panel pixel is (py, 479 - px). Top level:

  format        "x4pro-parity/1"
  name          the session's name: images/parity/<name>/, the report titles
  description   free text for the reader of the script (nothing reads it)
  firmware      {"stock_version", "dump" (repo-relative 16 MB device dump), "patches": [...]}
  card          {"mirror" (a copy of the device card's files), "size", "apps": [app dirs installed
                 with tools/xtapp.py], "files": [{"path" on the card, "make": "book", "title"}]}
  capture       {"pull": {"from", "to", "ms"}, "button": [px, py], "toast_ms", "toast_box",
                 "say": [runbook lines]}: how a Screen Capture is taken (pull the light panel down, tap
                 its button); the next input waits toast_ms of guest time, because the stock's
                 "Screenshot saved" toast (inside toast_box) is drawn over any screen painted while
                 it is alive, and compare names a difference that sits inside toast_box as one
  references    a directory beside the script holding <id>.png, the screen the emulator walk must
                 reach (`emu --update-references` writes them); a walk that reaches another screen
                 stops there
  masks         {name: {"box": [x0, y0, x1, y1], "why"}}: regions that may always differ (the clock,
                 the battery, the date). Boxes are half open: x0 <= x < x1, y0 <= y < y1.
  preconditions {name: {"check": {...}, "say", "why", "scope"}}: what must hold on the device for a
                 state region to compare (the bookshelf is empty, the gamebook has no saved game).
                 check kinds: absent {path} / same-dirs {path, expect} / same-files {path, like,
                 files} / same-as-mirror {path} -- all read off the mounted card by prepare-card --
                 or owner (a yes/no question in the runbook; pass `compare --not-met NAME` for a no).
                 scope "all" means every screen depends on it: unmet, the comparison is
                 inconclusive (exit 2) rather than explained.
  screens       [{"id", "title", "expect" (what the screen shows, in on-device words),
                  "steps": [...] (how to get there from the previous screen), "capture" (default
                  true), "leave": [...] (steps after it), "masks": [names], "state": [{"box",
                  "depends": precondition, "why"}], "note"}]

A step is {"do": "tap", "at": [px, py]} | {"do": "swipe", "from", "to", "ms"} | {"do": "home"} |
{"do": "press", "button": "left|right|power"} | {"do": "note"} (runbook only), each with "say" (the
runbook wording) and optional "settle" (seconds of idle panel to wait for afterwards, default 2) and
"repaint" (default true for tap/home/press: x4emu must see a refresh follow).

**How differences are explained.** Every differing pixel (grey difference > 64, as `x4emu screenshot
--diff`) is counted once, in the first region it falls in: a mask (explained: volatile content), a
state region whose precondition is unmet (explained: the card or the device differs, and prepare-card
or the owner said so), a state region whose precondition holds (unexplained), or nowhere
(unexplained). `compare` exits 0 when no screen has an unexplained pixel and every expected capture
was found, 1 otherwise, 2 when a scope-"all" precondition is unmet or the input is unusable.

**Pairing.** The device's new captures (precheck.json lists the ones that were on the card before the
session; `--since YYYYMMDD_HHMMSS` is the other filter) are sorted by their timestamped names. When
there are exactly as many as the script captures, they pair in order; otherwise (an extra capture, a
forgotten one) a monotonic alignment pairs each screen with the device capture closest to the
emulator's, skipping extras, and the report says what it skipped. In both cases a pair whose device
capture looks more like another screen is flagged.

Files of a session (`--session`, default images/parity/<script name>/, gitignored):
  emu/      NN-id.png (panel at the screen), NN-id-panel.png (light panel pulled over it), NN-id-post.png
            (panel right after the capture tap), NN-id.xic (the stock's capture), emu.json
  device/   precheck.json (prepare-card), the reader's .xic files (collect)
  report/   report.md, report.html, NN-id-{device,emu,diff}.png (compare)

The emulator runs a scratch copy of the dump: `tools/mkflash.py --raw`, `tools/nvsedit.py redact` at
once (CLAUDE.md rule 6), then `tools/stockpatch.py apply` with the script's patches; the copy is
deleted afterwards (`--keep` keeps it, in the session's emu/ directory). docs/parity.md is the manual.
"""
import argparse, datetime, hashlib, html, json, os, re, shutil, subprocess, sys, tempfile, time, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
PY = sys.executable
sys.path[:0] = [TOOLS, ROOT]    # the sibling tools, and the x4emu package (driven in-process)
import xic2png, xtapp           # noqa: E402  (both stdlib-only at import; PIL loads where a frame is decoded)

W, H = 480, 800                 # the portrait frame the UI is drawn in (and the .xic holds)
PANEL = (800, 480)              # x4emu screenshot: the landscape panel
DIFF_THRESHOLD = xic2png.DIFF_THRESHOLD          # 64, as `x4emu screenshot --diff`
CAPTURE_BYTES = xic2png.HEADER + W // 8 * H      # a mono 480x800 .xic: 24 + 48,000 = 48,024 bytes
CAPTURE_RE = re.compile(r'^screenshot_(\d{8}_\d{6})\.xic$')
FORMAT = 'x4pro-parity/1'
STEP_KINDS = {'tap': ('at',), 'swipe': ('from', 'to'), 'home': (), 'press': ('button',), 'note': ()}
CHECK_KINDS = {'absent': ('path',), 'same-dirs': ('path', 'expect'), 'same-files': ('path', 'like', 'files'),
               'same-as-mirror': ('path',), 'owner': ()}


class ParityError(Exception):
    """A script, a session directory or an emulator step that cannot go on; the message says why."""


# ---------------------------------------------------------------- the step script

def _box(v, where):
    if (not isinstance(v, list) or len(v) != 4 or not all(isinstance(n, int) for n in v)
            or not (0 <= v[0] < v[2] <= W and 0 <= v[1] < v[3] <= H)):
        raise ParityError(f'{where}: a box is [x0, y0, x1, y1] in the {W}x{H} portrait frame with '
                          f'x0 < x1 <= {W} and y0 < y1 <= {H}, not {v!r}')
    return tuple(v)


def _point(v, where):
    if (not isinstance(v, list) or len(v) != 2 or not all(isinstance(n, int) for n in v)
            or not (0 <= v[0] < W and 0 <= v[1] < H)):
        raise ParityError(f'{where}: a point is [px, py] inside the {W}x{H} portrait frame, not {v!r}')
    return tuple(v)


def _steps(steps, where):
    out = []
    for i, s in enumerate(steps or []):
        w = f'{where} step {i + 1}'
        if not isinstance(s, dict) or s.get('do') not in STEP_KINDS:
            raise ParityError(f'{w}: "do" must be one of {", ".join(STEP_KINDS)}')
        for k in STEP_KINDS[s['do']]:
            if k not in s:
                raise ParityError(f'{w}: a {s["do"]} step needs "{k}"')
        if not s.get('say'):
            raise ParityError(f'{w}: every step needs "say", the runbook wording')
        if s['do'] == 'tap':
            _point(s['at'], w)
        if s['do'] == 'swipe':
            _point(s['from'], w); _point(s['to'], w)
        if s['do'] == 'press' and s['button'] not in ('left', 'right', 'power'):
            raise ParityError(f'{w}: button must be left, right or power')
        out.append(s)
    return out


def load_script(path):
    """The step script at `path`, validated; raises ParityError naming the first problem."""
    try:
        raw = open(path, 'rb').read()
        sc = json.loads(raw)
    except (OSError, ValueError) as e:
        raise ParityError(f'{path}: {e}')
    if sc.get('format') != FORMAT:
        raise ParityError(f'{path}: "format" must be "{FORMAT}"')
    for k in ('name', 'firmware', 'card', 'capture', 'screens'):
        if k not in sc:
            raise ParityError(f'{path}: missing "{k}"')
    cap = sc['capture']
    _point(cap['pull']['from'], 'capture.pull'); _point(cap['pull']['to'], 'capture.pull')
    _point(cap['button'], 'capture.button')
    masks = sc.setdefault('masks', {})
    for n, m in masks.items():
        _box(m.get('box'), f'mask {n}')
    pre = sc.setdefault('preconditions', {})
    for n, p in pre.items():
        chk = p.get('check') or {}
        if chk.get('kind') not in CHECK_KINDS:
            raise ParityError(f'precondition {n}: check.kind must be one of {", ".join(CHECK_KINDS)}')
        for k in CHECK_KINDS[chk['kind']]:
            if k not in chk:
                raise ParityError(f'precondition {n}: a {chk["kind"]} check needs "{k}"')
        if not p.get('say'):
            raise ParityError(f'precondition {n}: needs "say" (the runbook wording)')
    ids = set()
    for i, s in enumerate(sc['screens']):
        w = f'screen {i + 1} ({s.get("id")})'
        if not s.get('id') or s['id'] in ids or not re.match(r'^[a-z0-9-]+$', s['id']):
            raise ParityError(f'{w}: every screen needs a unique "id" of a-z, 0-9 and -')
        ids.add(s['id'])
        s.setdefault('capture', True)
        s['steps'] = _steps(s.get('steps'), w)
        s['leave'] = _steps(s.get('leave'), w + ' leave')
        for n in s.setdefault('masks', []):
            if n not in masks:
                raise ParityError(f'{w}: unknown mask {n!r} (the script defines {", ".join(masks)})')
        for j, r in enumerate(s.setdefault('state', [])):
            _box(r.get('box'), f'{w} state {j + 1}')
            if r.get('depends') not in pre:
                raise ParityError(f'{w} state {j + 1}: "depends" must name a precondition '
                                  f'({", ".join(pre) or "none defined"})')
    if not any(s['capture'] for s in sc['screens']):
        raise ParityError(f'{path}: no screen is captured')
    sc['_path'] = os.path.abspath(path)
    sc['_sha256'] = hashlib.sha256(raw).hexdigest()
    return sc


def captured_screens(sc):
    return [s for s in sc['screens'] if s['capture']]


def screen_masks(sc, screen):
    """[(name, box)] of the masks that apply to a screen."""
    return [(n, tuple(sc['masks'][n]['box'])) for n in screen['masks']]


def landscape(px, py):
    """x4emu's landscape panel pixel for a portrait point: (py, 479 - px)."""
    return py, PANEL[1] - 1 - px


def default_session(sc):
    return os.path.join(ROOT, 'images', 'parity', sc['name'])


# ---------------------------------------------------------------- images and differences

def portrait(im):
    """An image in the portrait frame: a landscape panel shot (800x480) is turned upright (the inverse
    of docs/xic.md's ROTATE_90); a 480x800 image is taken as it is. Returned in mode L."""
    im = im.convert('L')
    if im.size == PANEL:
        from PIL import Image
        im = im.transpose(Image.Transpose.ROTATE_270)
    if im.size != (W, H):
        raise ParityError(f'an image of {im.size[0]}x{im.size[1]} is neither the {W}x{H} portrait frame '
                          f'nor the {PANEL[0]}x{PANEL[1]} panel')
    return im


def load_frame(path):
    """A .xic or a PNG, as the portrait frame (mode L)."""
    from PIL import Image
    if path.endswith('.xic'):
        try:
            im, _ = xic2png.decode_xic(open(path, 'rb').read())
        except xic2png.XicError as e:
            raise ParityError(f'{path}: {e}')
        return portrait(im)
    return portrait(Image.open(path))


def classify(a, b, masks=(), states=(), unmet=()):
    """Every pixel where `a` and `b` (portrait frames) differ, counted once in the first region it
    falls in: masks (always explained), then state regions (explained when their precondition is in
    `unmet`), then the rest (unexplained). `masks` is [(name, box)], `states` [(depends, box)].

    Returns {"total", "masked": {name: n}, "state": {depends: n}, "state_explained", "unexplained",
    "bbox": [x0, y0, x1, y1] of the unexplained pixels or None, "map": an L image (0 same, 1 masked,
    2 state explained, 3 unexplained)}."""
    from PIL import Image, ImageChops
    a = portrait(a); b = portrait(b)
    d = ImageChops.difference(a, b).point(lambda v: 255 if v > DIFF_THRESHOLD else 0)
    total = d.histogram()[255]
    rest = d.copy()
    cmap = Image.new('L', (W, H), 0)
    masked, state = {}, {}

    def take(box, code):
        crop = rest.crop(box)
        n = crop.histogram()[255]
        if n:
            cmap.paste(Image.new('L', crop.size, code), box[:2], crop)
            rest.paste(0, box)
        return n
    for name, box in masks:
        masked[name] = masked.get(name, 0) + take(box, 1)
    explained = 0
    for dep, box in states:
        n = take(box, 2 if dep in unmet else 3)
        state[dep] = state.get(dep, 0) + n
        if dep in unmet:
            explained += n
    held = sum(n for dep, n in state.items() if dep not in unmet)
    outside = rest.histogram()[255]
    if outside:
        cmap.paste(3, (0, 0), rest)
    bbox = list(cmap.point(lambda v: 255 if v == 3 else 0).getbbox()) if outside + held else None
    return {'total': total, 'masked': masked, 'state': state, 'state_explained': explained,
            'unexplained': outside + held, 'bbox': bbox, 'map': cmap}


def diff_picture(ref, result, masks=(), states=()):
    """An RGB picture of a comparison: `ref` lightened, region outlines (masks grey, state regions
    amber), differing pixels coloured: masked blue, explained state amber, unexplained red."""
    from PIL import Image, ImageDraw
    base = portrait(ref).point(lambda v: 160 + v * 95 // 255).convert('RGB')
    dr = ImageDraw.Draw(base)
    for _, (x0, y0, x1, y1) in masks:
        dr.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(150, 150, 150))
    for _, (x0, y0, x1, y1) in states:
        dr.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(220, 160, 0))
    colours = {1: (40, 90, 230), 2: (230, 160, 0), 3: (230, 20, 20)}
    m = result['map']
    for code, rgb in colours.items():
        sel = m.point(lambda v, c=code: 255 if v == c else 0)
        base.paste(rgb, (0, 0), sel)
    return base


# ---------------------------------------------------------------- the parity book

BOOK_DATE = (2026, 10, 12, 0, 0, 0)


def make_book(path, title='Parity Book', chapters=3, paragraphs=40):
    """A small EPUB 2, byte for byte the same on every run (fixed zip dates), so the copy on the reader
    and the copy on the emulated card are one file: same name, same size line in All Files, same text.
    The text is tests/mkepub.py's. Returns its SHA-256."""
    body = []
    for c in range(1, chapters + 1):
        paras = ''.join(f'<p>Chapter {c}, paragraph {p}. The quick brown fox jumps over the lazy dog. '
                        f'Pack my box with five dozen liquor jugs. Sphinx of black quartz, judge my vow.</p>\n'
                        for p in range(1, paragraphs + 1))
        body.append(f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
                    f'<head><title>Chapter {c}</title></head><body><h1>Chapter {c}</h1>\n{paras}</body></html>')
    uid = 'urn:uuid:9a7e1c2b-0000-4000-8000-706172697479'
    manifest = ''.join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                       for i in range(1, chapters + 1))
    spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(1, chapters + 1))
    nav = ''.join(f'<navPoint id="n{i}" playOrder="{i}"><navLabel><text>Chapter {i}</text></navLabel>'
                  f'<content src="c{i}.xhtml"/></navPoint>' for i in range(1, chapters + 1))
    opf = (f'<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" '
           f'unique-identifier="uid" version="2.0">\n<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>{title}</dc:title><dc:creator>x4pro-emu</dc:creator>\n<dc:language>en</dc:language>'
           f'<dc:identifier id="uid">{uid}</dc:identifier></metadata>\n<manifest>{manifest}<item id="ncx" '
           f'href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>\n<spine toc="ncx">{spine}'
           f'</spine></package>')
    ncx = (f'<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
           f'version="2005-1"><head><meta name="dtb:uid" content="{uid}"/></head>\n<docTitle><text>{title}'
           f'</text></docTitle><navMap>{nav}</navMap></ncx>')
    container = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:'
                 'opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    entries = [('mimetype', 'application/epub+zip', zipfile.ZIP_STORED),
               ('META-INF/container.xml', container, zipfile.ZIP_DEFLATED),
               ('OEBPS/content.opf', opf, zipfile.ZIP_DEFLATED),
               ('OEBPS/toc.ncx', ncx, zipfile.ZIP_DEFLATED)]
    entries += [(f'OEBPS/c{i}.xhtml', h, zipfile.ZIP_DEFLATED) for i, h in enumerate(body, 1)]
    with zipfile.ZipFile(path, 'w') as z:
        for name, text, comp in entries:
            info = zipfile.ZipInfo(name, BOOK_DATE)
            info.compress_type = comp
            info.external_attr = 0o644 << 16
            z.writestr(info, text)
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def card_files(sc, tmp):
    """[(card path, local file, its SHA-256)] of the script's generated card files, written into `tmp`."""
    out = []
    for i, f in enumerate(sc['card'].get('files', [])):
        if f.get('make') != 'book':
            raise ParityError(f'card file {f.get("path")}: "make" must be "book"')
        local = os.path.join(tmp, f'file{i}-' + os.path.basename(f['path']))
        out.append((f['path'].strip('/'), local, make_book(local, f.get('title', 'Parity Book'))))
    return out


# ---------------------------------------------------------------- the emulator walk

def run(cmd, what):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise ParityError(f'{what} failed ({r.returncode}): {" ".join(map(str, cmd))}\n{r.stdout}{r.stderr}')
    return r


def x4(name, *args, check=True):
    """One x4emu command on instance `name`, run in-process by x4emu/api.py: its `--json` object, which
    carries "error" when the command failed. No call is capped from here: every wait below passes its
    own --timeout or --wait."""
    os.environ.setdefault('X4EMU_ROOT', ROOT)            # as tools/x4emu does: drive this checkout
    from x4emu import api
    r = api.run([str(a) for a in args], name=name)
    if check and 'error' in r:
        raise ParityError(f'x4emu {" ".join(map(str, args))} failed: {r["error"]}')
    return r


def mt(img, tool, *args, check=True):
    """One mtools command on the card image's FAT32 partition (`xtapp.mtools`); with `check`, a failure
    raises ParityError carrying the tool's output."""
    r = xtapp.mtools(img, tool, *args, check=False)
    if check and r.returncode:
        raise ParityError(f'{tool} failed ({r.returncode}): {" ".join(map(str, args))}\n{r.stdout}{r.stderr}')
    return r


def card_captures(img):
    """The `screenshot_*.xic` names in /screenshots of a card image (read only; safe on a live card)."""
    r = mt(img, 'mdir', '-b', '::/screenshots', check=False)
    names = [os.path.basename(ln.strip()) for ln in r.stdout.splitlines()]
    return sorted(n for n in names if CAPTURE_RE.match(n))


def build_images(sc, work, dump=None, log=print):
    """The scratch flash (raw copy of the dump, redacted at once, patched) and the card (the mirror,
    the apps, the generated files) in `work`. Returns (flash, card)."""
    fw, card = sc['firmware'], sc['card']
    dump = dump or os.path.join(ROOT, fw['dump'])
    if not os.path.exists(dump):
        raise ParityError(f'{dump} not found: the device dump is not on this machine')
    flash = os.path.join(work, 'flash.bin')
    run([PY, os.path.join(TOOLS, 'mkflash.py'), flash, '--raw', dump], 'mkflash')
    run([PY, os.path.join(TOOLS, 'nvsedit.py'), flash, 'redact'], 'nvsedit redact')   # rule 6, at once
    import stockpatch
    try:
        ver = stockpatch.version_of(open(flash, 'rb').read())[1].version
    except stockpatch.StockPatchError as e:
        raise ParityError(f'{dump}: {e}')
    if ver != fw['stock_version']:
        raise ParityError(f'{dump} holds stock {ver}, the script is for {fw["stock_version"]}')
    if fw.get('patches'):
        run([PY, os.path.join(TOOLS, 'stockpatch.py'), 'apply', flash, *fw['patches']], 'stockpatch')
    log(f'flash: {flash} (stock {ver}, redacted, patched: {", ".join(fw.get("patches", [])) or "none"})')
    sd = os.path.join(work, 'sd.img')
    mirror = os.path.join(ROOT, card['mirror'])
    if not os.path.isdir(mirror):
        raise ParityError(f'{mirror} not found: the device card mirror is not on this machine')
    run([PY, os.path.join(TOOLS, 'mksd.py'), sd, '--size', card.get('size', '256M'), '--src', mirror,
         '--force'], 'mksd')
    for app in card.get('apps', []):
        rep = xtapp.install(sd, os.path.join(ROOT, app))
        log(f'card: {rep["card_dir"]} <- {app}')
    for path, local, _ in card_files(sc, work):
        parts = path.split('/')
        for i in range(1, len(parts)):
            mt(sd, 'mmd', '::/' + '/'.join(parts[:i]), check=False)
        mt(sd, 'mcopy', '-o', local, '::/' + path)
        log(f'card: /{path} ({os.path.getsize(local)} bytes)')
    return flash, sd


def emu_step(name, step, log):
    kind = step['do']
    if kind == 'note':
        return
    wait = ['--wait', '30'] if step.get('repaint', kind in ('tap', 'home', 'press')) else []
    if kind == 'tap':
        lx, ly = landscape(*step['at'])
        x4(name, 'tap', lx, ly, '--ms', step.get('ms', 120), '--quiet', '1', *wait)
    elif kind == 'swipe':
        (x1, y1), (x2, y2) = landscape(*step['from']), landscape(*step['to'])
        x4(name, 'swipe', x1, y1, x2, y2, '--ms', step.get('ms', 600), '--quiet', '1', *wait)
    elif kind == 'home':
        x4(name, 'home', '--quiet', '1', *wait)
    elif kind == 'press':
        x4(name, 'press', step['button'], '--quiet', '1', *wait)
    x4(name, 'wait-quiet', '--seconds', step.get('settle', 2), '--timeout', 120)
    log(f'    {kind} {step.get("at") or step.get("from") or step.get("button") or ""}: {step["say"]}')


def emu_capture(name, sc, card, base):
    """Pull the light panel, screenshot it, tap Screen Capture, screenshot right after, and wait for the
    new .xic on the card. Returns its name on the card."""
    cap = sc['capture']
    before = set(card_captures(card))
    pull = cap['pull']
    (x1, y1), (x2, y2) = landscape(*pull['from']), landscape(*pull['to'])
    x4(name, 'swipe', x1, y1, x2, y2, '--ms', pull.get('ms', 600), '--quiet', '1')
    x4(name, 'wait-quiet', '--seconds', 1.5, '--timeout', 60)
    x4(name, 'screenshot', base + '-panel.png')
    bx, by = landscape(*cap['button'])
    x4(name, 'tap', bx, by, '--ms', 120, '--wait', 20)
    t_cap = x4(name, 'state').get('uptime_us', 0) // 1000
    x4(name, 'screenshot', base + '-post.png')
    t0 = time.time()
    while time.time() - t0 < 30:
        new = sorted(set(card_captures(card)) - before)
        if new:
            break
        time.sleep(0.5)
    else:
        raise ParityError(f'no new /screenshots/screenshot_*.xic on the card 30 s after the Screen Capture '
                          f'tap ({base}-post.png shows the screen)')
    # the "Screenshot saved" toast stays alive ~3.5 s of guest time and is drawn over any screen
    # painted meanwhile (measured: a menu opened 3.4 s after the capture still carries it, 4.4 s
    # after not), so the next input waits `toast_ms` of guest time; the runbook says "count to five"
    x4(name, 'wait-guest-until', t_cap + cap.get('toast_ms', 5000), '--timeout', 60)
    x4(name, 'wait-quiet', '--seconds', 1, '--timeout', 60)
    return new[-1]


def boot(name, flash, sd, log):
    x4(name, 'stop', check=False)
    x4(name, 'run', '--flash', flash, '--sd', sd, '--boot-hold-power')
    x4(name, 'wait-text', 'main_task: Returned from app_main()', '--timeout', 90)
    x4(name, 'wait-refresh', '--total', 2, '--timeout', 120)
    x4(name, 'wait-quiet', '--seconds', 2, '--timeout', 60)
    log(f'booted {name}: Home painted')


def reference_path(sc, screen):
    """parity/<references>/<id>.png: the screen the emulator must reach (emulator-made, upright, 1 bpp),
    or None when the script names no reference directory."""
    if not sc.get('references'):
        return None
    return os.path.join(os.path.dirname(sc['_path']), sc['references'], screen['id'] + '.png')


def check_reference(sc, screen, shot, update, log):
    """Compare the screen the walk reached with its reference outside the masks (state regions count:
    the emulator's card is the script's). A difference means the steps no longer lead where they did
    -- a moved row, a firmware or model change -- and the walk stops there. `update` rewrites it."""
    ref = reference_path(sc, screen)
    if not ref:
        return None
    if update:
        os.makedirs(os.path.dirname(ref), exist_ok=True)
        load_frame(shot).point(lambda v: 255 if v > 127 else 0).convert('1').save(ref)
        log(f'    reference written: {os.path.relpath(ref, ROOT)}')
        return {'updated': True}
    if not os.path.exists(ref):
        log(f'    no reference {os.path.relpath(ref, ROOT)} (emu --update-references writes it)')
        return {'missing': True}
    r = classify(load_frame(shot), load_frame(ref), screen_masks(sc, screen))
    if r['unexplained']:
        raise ParityError(f'{screen["id"]}: the walk reached a screen {r["unexplained"]} px away from '
                          f'{os.path.relpath(ref, ROOT)} outside the masks (inside x {r["bbox"][0]}..'
                          f'{r["bbox"][2] - 1}, y {r["bbox"][1]}..{r["bbox"][3] - 1}); the steps no longer '
                          f'lead there. The screenshot is {shot}')
    return {'unexplained': 0, 'masked': r['masked']}


def cmd_emu(a, log=print):
    sc = load_script(a.script)
    out = os.path.abspath(a.out or os.path.join(a.session or default_session(sc), 'emu'))
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):                     # a fresh walk never mixes with an old one
        if re.match(r'^\d\d-.*\.(png|xic)$', f) or f == 'emu.json':
            os.remove(os.path.join(out, f))
    work = tempfile.mkdtemp(prefix='parity-', dir=a.work)
    name = a.name
    manifest = {'script': os.path.relpath(sc['_path'], ROOT), 'script_sha256': sc['_sha256'],
                'stock_version': sc['firmware']['stock_version'], 'instance': name,
                'started': datetime.datetime.now().isoformat(timespec='seconds'), 'screens': []}
    ok = True
    try:
        flash, sd = build_images(sc, work, a.dump, log)
        boot(name, flash, sd, log)
        for i, s in enumerate(sc['screens'], 1):
            base = os.path.join(out, f'{i:02d}-{s["id"]}')
            log(f'{i:02d} {s["id"]}: {s.get("title", "")}')
            for st in s['steps']:
                emu_step(name, st, log)
            x4(name, 'screenshot', base + '.png')
            entry = {'index': i, 'id': s['id'], 'title': s.get('title', ''), 'capture': s['capture'],
                     'screen_png': os.path.basename(base) + '.png'}
            entry['reference'] = check_reference(sc, s, base + '.png', a.update_references, log)
            if s['capture']:
                entry['card_name'] = emu_capture(name, sc, sd, base)
                entry['panel_png'] = os.path.basename(base) + '-panel.png'
                entry['post_png'] = os.path.basename(base) + '-post.png'
                log(f'    captured {entry["card_name"]}')
            for st in s['leave']:
                emu_step(name, st, log)
            manifest['screens'].append(entry)
        state = x4(name, 'state')
        manifest['epd_unknown_cmds'] = state.get('epd_unknown_cmds')
        x4(name, 'stop')                           # flush the card before mtools copies from it
        for e, s in zip(manifest['screens'], sc['screens']):
            if not e['capture']:
                continue
            dst = os.path.join(out, f'{e["index"]:02d}-{e["id"]}.xic')
            mt(sd, 'mcopy', '-o', '::/screenshots/' + e['card_name'], dst)
            e['xic'] = os.path.basename(dst)
            size = os.path.getsize(dst)
            frame = load_frame(dst)
            res = classify(frame, load_frame(os.path.join(out, e['screen_png'])), screen_masks(sc, s))
            post = classify(frame, load_frame(os.path.join(out, e['post_png'])))
            e['check'] = {'xic_bytes': size, 'vs_screen': {k: res[k] for k in ('total', 'masked', 'unexplained', 'bbox')},
                          'vs_post_total': post['total']}
            e['ok'] = size == CAPTURE_BYTES and res['unexplained'] == 0
            ok &= e['ok']
            log(f'{e["index"]:02d} {e["id"]}: .xic vs panel {res["unexplained"]} px outside the masks '
                f'({res["total"]} in all; masked {res["masked"]}); vs the panel right after the tap '
                f'{post["total"]} px' + ('' if e['ok'] else '  <-- MISMATCH'))
    finally:
        x4(name, 'stop', check=False)
        if a.keep:
            for f in ('flash.bin', 'sd.img'):
                if os.path.exists(os.path.join(work, f)):
                    shutil.move(os.path.join(work, f), os.path.join(out, f))
            log(f'kept flash.bin (redacted) and sd.img in {out}')
        shutil.rmtree(work, ignore_errors=True)
    manifest['ok'] = ok
    manifest['finished'] = datetime.datetime.now().isoformat(timespec='seconds')
    json.dump(manifest, open(os.path.join(out, 'emu.json'), 'w'), indent=1)
    log(f'{"OK" if ok else "MISMATCH"}: {sum(1 for e in manifest["screens"] if e.get("ok"))} of '
        f'{len(captured_screens(sc))} captures equal their panel screenshot; {out}/emu.json')
    return 0 if ok else 1


# ---------------------------------------------------------------- the device card (USB mode)

def check_precondition(p, mount, sc):
    """(met, evidence) of one card precondition against the mounted card; (None, why) for owner ones."""
    chk = p['check']
    kind = chk['kind']
    if kind == 'owner':
        return None, 'asked in the runbook; pass `compare --not-met NAME` when the answer was no'
    path = os.path.join(mount, chk['path'])
    if kind == 'absent':
        there = os.path.exists(path)
        return not there, f'/{chk["path"]} {"exists" if there else "is absent"}'
    if kind == 'same-dirs':
        have = sorted(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))
                      and not d.startswith('.')) if os.path.isdir(path) else []
        return have == sorted(chk['expect']), f'/{chk["path"]} holds {have or "nothing"}'
    if kind == 'same-files':
        like = os.path.join(ROOT, chk['like'])
        bad = []
        for f in chk['files']:
            a, b = os.path.join(path, f), os.path.join(like, f)
            if not os.path.exists(a) or open(a, 'rb').read() != open(b, 'rb').read():
                bad.append(f)
        return not bad, (f'/{chk["path"]} matches {chk["like"]}' if not bad
                         else f'/{chk["path"]}: {", ".join(bad)} differ from {chk["like"]} or are missing')
    if kind == 'same-as-mirror':
        ref = os.path.join(ROOT, sc['card']['mirror'], chk['path'])
        same = os.path.exists(path) and os.path.exists(ref) and open(path, 'rb').read() == open(ref, 'rb').read()
        return same, f'/{chk["path"]} {"equals" if same else "differs from"} the mirror\'s'
    raise ParityError(f'unknown check {kind}')


def looks_like_reader_card(mount):
    return os.path.isdir(mount) and any(os.path.isdir(os.path.join(mount, d)) for d in ('XTData', 'XTCache'))


def cmd_prepare_card(a, log=print):
    """Read the mounted card: the preconditions, the captures already there; with --yes also copy the
    script's generated files onto it. Writes precheck.json into the session's device/ directory."""
    sc = load_script(a.script)
    mount = a.mount
    if not looks_like_reader_card(mount):
        raise ParityError(f'{mount} does not look like the reader\'s card (no XTData or XTCache folder); '
                          'nothing read or written')
    dev = os.path.abspath(a.out or os.path.join(a.session or default_session(sc), 'device'))
    os.makedirs(dev, exist_ok=True)
    shots = os.path.join(mount, 'screenshots')
    existing = sorted(n for n in os.listdir(shots) if CAPTURE_RE.match(n)) if os.path.isdir(shots) else []
    pre = {}
    for n, p in sc['preconditions'].items():
        met, ev = check_precondition(p, mount, sc)
        pre[n] = {'met': met, 'evidence': ev, 'scope': p.get('scope', 'regions')}
        log(f'precondition {n}: {"met" if met else "NOT met" if met is False else "ask the owner"} ({ev})')
    tmp = tempfile.mkdtemp(prefix='parity-card-')
    plan, written = [], []
    try:
        for path, local, want in card_files(sc, tmp):
            dst = os.path.join(mount, path)
            have = hashlib.sha256(open(dst, 'rb').read()).hexdigest() if os.path.exists(dst) else None
            if have == want:
                log(f'card: /{path} already there, identical (sha256 {want[:16]}...)')
                continue
            plan.append((path, local, dst, want, have))
            log(f'plan: write /{path} ({os.path.getsize(local)} bytes, sha256 {want[:16]}...)'
                + (f', replacing a different file of that name' if have else ''))
        if plan and not a.yes:
            log('pass --yes to write; nothing has been written to the card')
        if plan and a.yes:
            for path, local, dst, want, _ in plan:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(local, dst)        # data only: no xattrs, so macOS writes no ._ file
                with open(dst, 'rb') as f:
                    os.fsync(f.fileno())
                got = hashlib.sha256(open(dst, 'rb').read()).hexdigest()
                if got != want:
                    raise ParityError(f'/{path}: read back sha256 {got}, wrote {want}')
                dot = os.path.join(os.path.dirname(dst), '._' + os.path.basename(dst))
                if os.path.exists(dot):            # an AppleDouble file next to the one we wrote
                    os.remove(dot)
                written.append(path)
                log(f'wrote /{path}, read back identical')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rec = {'checked': datetime.datetime.now().isoformat(timespec='seconds'), 'mount': mount,
           'script': os.path.relpath(sc['_path'], ROOT), 'preconditions': pre, 'existing_captures': existing,
           'written': written, 'pending': [p[0] for p in plan if p[0] not in written]}
    out = os.path.join(dev, 'precheck.json')
    json.dump(rec, open(out, 'w'), indent=1)
    log(f'{len(existing)} capture(s) already on the card (compare ignores them); {out}')
    return 0 if not rec['pending'] else 1


def cmd_collect(a, log=print):
    """Copy the session's new captures off the mounted card (read only on the card)."""
    sc = load_script(a.script)
    dev = os.path.abspath(a.out or os.path.join(a.session or default_session(sc), 'device'))
    os.makedirs(dev, exist_ok=True)
    pre = os.path.join(dev, 'precheck.json')
    old = set(json.load(open(pre)).get('existing_captures', [])) if os.path.exists(pre) else set()
    shots = os.path.join(a.mount, 'screenshots')
    if not os.path.isdir(shots):
        raise ParityError(f'{shots} not found: is the reader in USB mode and its card mounted?')
    new = sorted(n for n in os.listdir(shots) if CAPTURE_RE.match(n) and n not in old)
    for n in new:
        shutil.copyfile(os.path.join(shots, n), os.path.join(dev, n))
        log(f'copied {n} ({os.path.getsize(os.path.join(dev, n))} bytes)')
    want = len(captured_screens(sc))
    log(f'{len(new)} new capture(s), the script takes {want}' + ('' if len(new) == want else
        ' -- compare will align them by content and say which it skipped or misses'))
    if not os.path.exists(pre):
        log('no precheck.json: every capture on the card was copied; pass `compare --since` to drop old ones')
    return 0


# ---------------------------------------------------------------- compare

def device_captures(d, existing=(), since=None):
    existing = set(existing)
    names = sorted(n for n in os.listdir(d) if CAPTURE_RE.match(n) and n not in existing)
    if since:
        names = [n for n in names if CAPTURE_RE.match(n).group(1) >= since]
    return names


def align(cost, skip_screen=1.0):
    """Monotonic alignment of screens (rows) with device captures (columns): each screen takes at most
    one capture, in order, captures may be skipped for free, a screen without one costs `skip_screen`.
    Returns [column or None] per row."""
    n, m = len(cost), len(cost[0]) if cost else 0
    INF = float('inf')
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        best[0][j] = 0.0
    for i in range(1, n + 1):
        for j in range(m + 1):
            opts = [(best[i - 1][j] + skip_screen, 'skip-screen')]
            if j:
                opts.append((best[i][j - 1], 'skip-capture'))
                opts.append((best[i - 1][j - 1] + cost[i - 1][j - 1], 'pair'))
            best[i][j], back[i][j] = min(opts, key=lambda t: t[0])
    out, i, j = [None] * n, n, m
    while i > 0:
        mv = back[i][j]
        if mv == 'pair':
            out[i - 1] = j - 1; i -= 1; j -= 1
        elif mv == 'skip-capture':
            j -= 1
        else:
            i -= 1
    return out


def pair_captures(sc, emu_frames, dev_frames):
    """[(screen index into captured_screens, device index or None)], notes. Order when the counts
    agree, content alignment otherwise; a pair whose device frame matches another screen better is
    noted either way."""
    screens = captured_screens(sc)
    cost = []
    for s, ef in zip(screens, emu_frames):
        row = []
        for df in dev_frames:
            r = classify(df, ef, screen_masks(sc, s))
            row.append(r['total'] / float(W * H))
        cost.append(row)
    notes = []
    if len(dev_frames) == len(screens):
        pairs = list(range(len(screens)))
        how = 'order'
    else:
        pairs = align(cost, skip_screen=0.25)
        how = 'content'
        used = {j for j in pairs if j is not None}
        skipped = [j for j in range(len(dev_frames)) if j not in used]
        if skipped:
            notes.append(f'{len(skipped)} device capture(s) matched no screen and were skipped: '
                         f'#{", #".join(str(j + 1) for j in skipped)}')
    for i, j in enumerate(pairs):
        if j is None:
            continue
        k = min(range(len(screens)), key=lambda r: cost[r][j])
        if k != i and cost[k][j] + 0.01 < cost[i][j]:
            notes.append(f'device capture #{j + 1} is paired with {screens[i]["id"]} but looks more like '
                         f'{screens[k]["id"]} ({100 * cost[k][j]:.2f} % against {100 * cost[i][j]:.2f} %): '
                         f'check the order the captures were taken in')
    return pairs, how, notes


def cmd_compare(a, log=print):
    sc = load_script(a.script)
    emu_dir, dev_dir = os.path.abspath(a.emu_dir), os.path.abspath(a.device_dir)
    mf = os.path.join(emu_dir, 'emu.json')
    if not os.path.exists(mf):
        raise ParityError(f'{mf} not found: run `parity.py emu {a.script}` first')
    man = json.load(open(mf))
    if man.get('script_sha256') != sc['_sha256']:
        log(f'warning: {emu_dir} was made from a different version of {a.script}')
    out = os.path.abspath(a.out or os.path.join(os.path.dirname(emu_dir), 'report'))
    os.makedirs(out, exist_ok=True)
    pre_path = a.precheck or os.path.join(dev_dir, 'precheck.json')
    pre = json.load(open(pre_path)) if os.path.exists(pre_path) else None
    unmet = set(a.not_met or [])
    pre_rows = []
    for n, p in sc['preconditions'].items():
        rec = (pre or {}).get('preconditions', {}).get(n)
        if n in unmet:
            met, ev = False, 'stated with --not-met'
        elif rec and rec['met'] is not None:
            met, ev = rec['met'], rec['evidence']
        elif p['check']['kind'] == 'owner':
            met, ev = True, 'owner question, answered yes (no --not-met)'
        else:
            met, ev = None, 'not checked: no precheck.json'
        if met is False:
            unmet.add(n)
        pre_rows.append({'name': n, 'met': met, 'evidence': ev, 'scope': p.get('scope', 'regions'),
                         'say': p['say']})
    global_unmet = [r['name'] for r in pre_rows if r['met'] is False and r['scope'] == 'all']
    unchecked = [r['name'] for r in pre_rows if r['met'] is None]

    entries = {e['id']: e for e in man['screens']}
    screens = captured_screens(sc)
    emu_frames = []
    for s in screens:
        e = entries.get(s['id'])
        if not e or not e.get('xic'):
            raise ParityError(f'{mf} has no capture for screen {s["id"]}')
        emu_frames.append(load_frame(os.path.join(emu_dir, e['xic'])))
    names = device_captures(dev_dir, (pre or {}).get('existing_captures', ()), a.since)
    dev_frames = [load_frame(os.path.join(dev_dir, n)) for n in names]
    grey = [n for n in names if xic2png.parse_header(open(os.path.join(dev_dir, n), 'rb').read())['planes'] != 1]
    if not names:
        raise ParityError(f'no device captures (screenshot_*.xic) in {dev_dir}'
                          + (' beyond the ones precheck.json lists as old' if pre else ''))
    pairs, how, notes = pair_captures(sc, emu_frames, dev_frames)
    if grey:
        notes.append(f'{len(grey)} device capture(s) are 4-level grey (2 planes): {", ".join(grey)}. Every '
                     f'emulator capture so far is mono, and the grey decode is xic2png\'s documented guess '
                     f'(docs/xic.md), so read their differences with that in mind')

    rows, all_ok = [], True
    for i, s in enumerate(sc['screens'], 1):
        e = entries.get(s['id'], {})
        row = {'index': i, 'id': s['id'], 'title': s.get('title', ''), 'capture': s['capture'],
               'note': s.get('note', ''), 'emu_screen': e.get('screen_png'), 'emu_panel': e.get('panel_png')}
        if e.get('screen_png') and os.path.exists(os.path.join(emu_dir, e['screen_png'])):
            load_frame(os.path.join(emu_dir, e['screen_png'])).save(os.path.join(out, f'{i:02d}-{s["id"]}-emu-panel.png'))
            row['emu_panel_copy'] = f'{i:02d}-{s["id"]}-emu-panel.png'
        if not s['capture']:
            row['verdict'] = 'not captured'
            rows.append(row)
            continue
        k = screens.index(s)
        j = pairs[k]
        masks = screen_masks(sc, s)
        states = [(r['depends'], tuple(r['box'])) for r in s['state']]
        ef = emu_frames[k]
        ef.save(os.path.join(out, f'{i:02d}-{s["id"]}-emu.png'))
        row['emu_png'] = f'{i:02d}-{s["id"]}-emu.png'
        if j is None:
            row['verdict'] = 'missing'
            row['why'] = 'no device capture was paired with this screen'
            all_ok = False
            rows.append(row)
            continue
        df = dev_frames[j]
        res = classify(df, ef, masks, states, unmet)
        df.save(os.path.join(out, f'{i:02d}-{s["id"]}-device.png'))
        diff_picture(df, res, masks, states).save(os.path.join(out, f'{i:02d}-{s["id"]}-diff.png'))
        row.update({'device_xic': names[j], 'device_png': f'{i:02d}-{s["id"]}-device.png',
                    'diff_png': f'{i:02d}-{s["id"]}-diff.png',
                    **{k2: res[k2] for k2 in ('total', 'masked', 'state', 'state_explained', 'unexplained', 'bbox')}})
        expl = [f'{n} px in {m} ({sc["masks"][m]["why"]})' for m, n in res['masked'].items() if n]
        expl += [f'{n} px in a region that depends on {d} (not met: '
                 f'{next(r["evidence"] for r in pre_rows if r["name"] == d)})'
                 for d, n in res['state'].items() if n and d in unmet]
        held = [f'{n} px in a region that depends on {d}, which holds' for d, n in res['state'].items()
                if n and d not in unmet]
        row['explained'] = expl
        row['held'] = held
        if res['unexplained']:
            row['verdict'] = 'DIFFERS'
            all_ok = False
            tb = sc['capture'].get('toast_box')
            if tb and res['bbox'] and (tb[0] <= res['bbox'][0] and tb[1] <= res['bbox'][1]
                                       and res['bbox'][2] <= tb[2] and res['bbox'][3] <= tb[3]):
                row['hint'] = ('the difference sits where the "Screenshot saved" toast is drawn: the '
                               'previous capture\'s toast was probably still alive when this screen was '
                               'drawn; retake this screen with a pause of five seconds before it')
        else:
            row['verdict'] = 'identical' if res['total'] == 0 else 'explained'
        rows.append(row)

    status = 'inconclusive' if global_unmet else ('pass' if all_ok else 'fail')
    rep = {'script': os.path.relpath(sc['_path'], ROOT), 'stock_version': sc['firmware']['stock_version'],
           'emu_dir': emu_dir, 'device_dir': dev_dir, 'pairing': how, 'notes': notes,
           'device_captures': names, 'preconditions': pre_rows, 'unchecked': unchecked,
           'status': status, 'made': datetime.datetime.now().isoformat(timespec='seconds'),
           'screens': rows}
    json.dump(rep, open(os.path.join(out, 'report.json'), 'w'), indent=1)
    open(os.path.join(out, 'report.md'), 'w').write(report_md(sc, rep))
    open(os.path.join(out, 'report.html'), 'w').write(report_html(sc, rep))
    for r in rows:
        log(f'{r["index"]:02d} {r["id"]:<12} {r["verdict"]:<12} '
            + (f'{r.get("unexplained", 0)} unexplained of {r.get("total", 0)}' if r['capture'] and 'total' in r else ''))
    for n in notes:
        log(f'note: {n}')
    if global_unmet:
        log(f'INCONCLUSIVE: {", ".join(global_unmet)} not met, so every screen is expected to differ')
    log(f'{status.upper()}: {out}/report.html')
    return {'pass': 0, 'fail': 1, 'inconclusive': 2}[status]


def _verdict_line(r):
    if not r['capture']:
        return 'not captured' + (f' -- {r["note"]}' if r.get('note') else '')
    if r['verdict'] == 'missing':
        return 'MISSING: no device capture paired with it'
    s = f'{r["verdict"]}: {r["total"]} px differ, {r["unexplained"]} unexplained'
    if r.get('bbox'):
        s += f' (inside x {r["bbox"][0]}..{r["bbox"][2] - 1}, y {r["bbox"][1]}..{r["bbox"][3] - 1})'
    return s


def report_md(sc, rep):
    L = [f'# Device parity: {sc["name"]}', '',
         f'Status: **{rep["status"].upper()}**. Stock {rep["stock_version"]}, script `{rep["script"]}`, made '
         f'{rep["made"]}. Device captures: {len(rep["device_captures"])}, paired by {rep["pairing"]}.', '']
    for n in rep['notes']:
        L.append(f'- note: {n}')
    L += ['', '## Preconditions', '', '| name | met | evidence |', '|---|---|---|']
    for p in rep['preconditions']:
        L.append(f'| {p["name"]} | {"yes" if p["met"] else "no" if p["met"] is False else "unchecked"} | '
                 f'{p["evidence"]} |')
    L += ['', '## Screens', '', '| # | screen | device file | verdict | explained |', '|---|---|---|---|---|']
    for r in rep['screens']:
        L.append(f'| {r["index"]} | {r["id"]} | {r.get("device_xic", "")} | {_verdict_line(r)} | '
                 f'{"; ".join(r.get("explained", [])) or ""} |')
    for r in rep['screens']:
        L += ['', f'### {r["index"]:02d} {r["id"]}: {r["title"]}', '', _verdict_line(r), '']
        for x in r.get('explained', []):
            L.append(f'- explained: {x}')
        for x in r.get('held', []):
            L.append(f'- **unexplained**: {x}')
        if r.get('hint'):
            L.append(f'- hint: {r["hint"]}')
        if r.get('device_png'):
            L.append(f'![device]({r["device_png"]}) ![emulator]({r["emu_png"]}) ![diff]({r["diff_png"]})')
        elif r.get('emu_panel_copy'):
            L.append(f'![emulator panel]({r["emu_panel_copy"]})')
    L += ['', 'Diff pictures: the device capture lightened; blue = masked (volatile), amber = explained by '
          'an unmet precondition, red = unexplained; grey outlines are masks, amber outlines state regions.', '']
    return '\n'.join(L)


def report_html(sc, rep):
    e = html.escape
    css = ('body{font:14px/1.45 -apple-system,Helvetica,sans-serif;margin:24px;color:#222;background:#fff}'
           'table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px;'
           'vertical-align:top;text-align:left}img{width:240px;border:1px solid #999;image-rendering:pixelated}'
           '.ok{color:#176b2c}.bad{color:#b00020;font-weight:600}.inc{color:#8a5a00;font-weight:600}'
           'figure{display:inline-block;margin:0 12px 12px 0}figcaption{font-size:12px;color:#555}'
           '@media(prefers-color-scheme:dark){body{background:#161616;color:#ddd}td,th{border-color:#444}}')
    cls = {'pass': 'ok', 'fail': 'bad', 'inconclusive': 'inc'}[rep['status']]
    H = [f'<!doctype html><meta charset="utf-8"><title>Parity {e(sc["name"])}</title><style>{css}</style>',
         f'<h1>Device parity: {e(sc["name"])}</h1>',
         f'<p class="{cls}">Status: {rep["status"].upper()}</p>',
         f'<p>Stock {e(rep["stock_version"])}, script <code>{e(rep["script"])}</code>, made {e(rep["made"])}; '
         f'{len(rep["device_captures"])} device capture(s), paired by {e(rep["pairing"])}.</p>']
    if rep['notes']:
        H.append('<ul>' + ''.join(f'<li>{e(n)}</li>' for n in rep['notes']) + '</ul>')
    H.append('<h2>Preconditions</h2><table><tr><th>name</th><th>met</th><th>evidence</th><th>what it is</th></tr>')
    for p in rep['preconditions']:
        m = 'yes' if p['met'] else 'no' if p['met'] is False else 'unchecked'
        H.append(f'<tr><td>{e(p["name"])}</td><td>{m}</td><td>{e(p["evidence"])}</td><td>{e(p["say"])}</td></tr>')
    H.append('</table><h2>Screens</h2><table><tr><th>#</th><th>screen</th><th>device file</th><th>verdict</th></tr>')
    for r in rep['screens']:
        c = 'bad' if r.get('verdict') in ('DIFFERS', 'missing') else 'ok'
        H.append(f'<tr><td>{r["index"]}</td><td><a href="#s{r["index"]}">{e(r["id"])}</a></td>'
                 f'<td>{e(r.get("device_xic", ""))}</td><td class="{c}">{e(_verdict_line(r))}</td></tr>')
    H.append('</table>')
    for r in rep['screens']:
        H.append(f'<h3 id="s{r["index"]}">{r["index"]:02d} {e(r["id"])}: {e(r["title"])}</h3>'
                 f'<p>{e(_verdict_line(r))}</p>')
        items = ([f'explained: {x}' for x in r.get('explained', [])] + [f'UNEXPLAINED: {x}' for x in r.get('held', [])]
                 + ([f'hint: {r["hint"]}'] if r.get('hint') else []))
        if items:
            H.append('<ul>' + ''.join(f'<li>{e(x)}</li>' for x in items) + '</ul>')
        figs = []
        for key, cap in (('device_png', 'device .xic'), ('emu_png', 'emulator .xic'), ('diff_png', 'difference'),
                         ('emu_panel_copy', 'emulator panel')):
            if r.get(key):
                figs.append(f'<figure><img src="{e(r[key])}" alt="{e(cap)}"><figcaption>{e(cap)}</figcaption></figure>')
        H.append(''.join(figs))
    H.append('<p>Difference pictures: the device capture lightened; <span style="color:#285ae6">blue</span> = '
             'masked (volatile), <span style="color:#e6a000">amber</span> = explained by an unmet precondition, '
             '<span style="color:#e61414">red</span> = unexplained.</p>')
    return '\n'.join(H) + '\n'


# ---------------------------------------------------------------- the runbook

def runbook_md(sc):
    cap = sc['capture']
    caps_total = len(captured_screens(sc))
    L = [f'# Parity session: {sc["name"]} -- the owner\'s checklist', '',
         f'You walk {len(sc["screens"])} screens on the reader and take {caps_total} Screen Captures; the '
         'computer walks the same screens in the emulator and compares the pictures. Nothing here needs '
         'the computer except the two USB-mode copies, at the start and at the end. Take your time: after '
         'every tap, wait until the screen has finished drawing before the next one. If a step goes '
         'wrong, say so -- a wrong screen is cheaper to redo than to explain.', '',
         '## How to take a Screen Capture (used at the end of most steps)', '']
    for k, line in enumerate(cap['say'], 1):
        L.append(f'{k}. {line}')
    L += ['', '## Before you start: questions (answer yes or no, out loud or on paper)', '']
    q = [p for p in sc['preconditions'].values() if p['check']['kind'] == 'owner']
    for k, p in enumerate(q, 1):
        L.append(f'{k}. {p["say"]}')
    if not q:
        L.append('(none)')
    L += ['', 'The card itself is checked by the computer when it is mounted (USB mode, step 0).', '',
          '## The walk', '']
    n = 0
    L.append(f'{n}. **The developer version (the coordinator).** The coordinator writes the stock firmware '
             f'with Screen Capture switched on and the reader restarts (press the power button if it stays '
             f'dark). Check: slide a finger down from the top edge; the light panel now has three boxed '
             f'buttons at the bottom (Memory Developer, Screen Ca, BGIO Developer). Tap the dimmed page to '
             f'close it.')
    L.append(f'   **Card check (with the coordinator).** On the reader: menu (≡) -> **USB**, connect, and let '
             f'the computer run `tools/parity.py prepare-card {os.path.relpath(sc["_path"], ROOT)} '
             f'"/Volumes/NO NAME" --yes`. It copies the parity book onto the card and notes what is on '
             f'the card. Then eject the card on the computer and leave USB mode on the reader.')
    caps = 0
    for s in sc['screens']:
        n += 1
        L.append(f'{n}. **{s.get("title", s["id"])}**')
        for st in s['steps']:
            L.append(f'   - {st["say"]}')
        if s.get('expect'):
            L.append(f'   - You should now see: {s["expect"]}.')
        if s['capture']:
            caps += 1
            L.append(f'   - Take a Screen Capture (capture {caps} of {caps_total}), then count to five.')
        elif s.get('note'):
            L.append(f'   - No capture here: {s["note"]}.')
        for st in s['leave']:
            L.append(f'   - {st["say"]}')
    n += 1
    L += [f'{n}. **Copy the captures off (with the coordinator).** Touch the Home pad until the Bookshelf '
          f'appears, then ≡ -> **USB** again; the computer runs '
          f'`tools/parity.py collect {os.path.relpath(sc["_path"], ROOT)} "/Volumes/NO NAME"`. It only reads the '
          f'card. You should have taken {caps} captures; it is fine if one is extra or missing, the report '
          f'says which.', '',
          'When something on the reader looks different from what the step says, stop and tell the '
          'coordinator; that difference is what this session is for.', '']
    return '\n'.join(L)


def cmd_runbook(a, log=print):
    sc = load_script(a.script)
    text = runbook_md(sc)
    if a.out:
        open(a.out, 'w').write(text)
        log(a.out)
    else:
        sys.stdout.write(text)
    return 0


def cmd_book(a, log=print):
    log(f'{a.out}: sha256 {make_book(a.out, a.title)}')
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('emu', help='walk the script in the emulator and check every .xic against its panel shot')
    p.add_argument('script'); p.add_argument('--session'); p.add_argument('--out', help='the emu directory (default SESSION/emu)')
    p.add_argument('--dump', help='the 16 MB device dump (default: the script\'s firmware.dump)')
    p.add_argument('--name', default='parity', help='x4emu instance name (default parity)')
    p.add_argument('--work', help='where the scratch flash and card are built (default: a temp directory)')
    p.add_argument('--keep', action='store_true', help='keep the redacted flash copy and the card in the emu directory')
    p.add_argument('--update-references', action='store_true',
                   help='write each screen the walk reaches as the script\'s reference instead of checking it')
    p.set_defaults(fn=cmd_emu)
    p = sp.add_parser('runbook', help='the owner\'s numbered checklist (Markdown)')
    p.add_argument('script'); p.add_argument('--out'); p.set_defaults(fn=cmd_runbook)
    p = sp.add_parser('prepare-card', help='check the mounted reader card; --yes also copies the parity files')
    p.add_argument('script'); p.add_argument('mount'); p.add_argument('--session')
    p.add_argument('--out', help='directory for precheck.json (default SESSION/device)')
    p.add_argument('--yes', action='store_true', help='write the script\'s files onto the card')
    p.set_defaults(fn=cmd_prepare_card)
    p = sp.add_parser('collect', help='copy the session\'s new captures off the mounted card (read only)')
    p.add_argument('script'); p.add_argument('mount'); p.add_argument('--session')
    p.add_argument('--out', help='destination (default SESSION/device)')
    p.set_defaults(fn=cmd_collect)
    p = sp.add_parser('compare', help='pair device and emulator captures, diff them, write the report')
    p.add_argument('script'); p.add_argument('device_dir'); p.add_argument('emu_dir')
    p.add_argument('--out', help='report directory (default: report/ beside EMU_DIR)')
    p.add_argument('--precheck', help='precheck.json (default DEVICE_DIR/precheck.json)')
    p.add_argument('--not-met', action='append', metavar='NAME', help='a precondition that did not hold')
    p.add_argument('--since', metavar='YYYYMMDD_HHMMSS', help='ignore device captures named before this')
    p.set_defaults(fn=cmd_compare)
    p = sp.add_parser('book', help='write the parity book')
    p.add_argument('out'); p.add_argument('--title', default='Parity Book'); p.set_defaults(fn=cmd_book)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except ParityError as e:
        print(f'parity: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
