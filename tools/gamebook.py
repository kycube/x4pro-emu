#!/usr/bin/env python3
r"""Gamebooks for the stock firmware's Lua host: a story file and pixel-art scenes -> one app.

  tools/gamebook.py check  STORY_DIR              parse and validate; print the passage graph
  tools/gamebook.py build  STORY_DIR OUT_DIR      OUT_DIR/{index.lua, manifest.json, app.xtapp}
  tools/gamebook.py plates STORY_DIR OUT_DIR      the illustrations and lettering as PNG, to look at

STORY_DIR holds `story.txt` and `scenes.py` (a `SCENES` dict of functions returning
`tools/pixart.Scene` objects). OUT_DIR's basename must be the story's `app_id`: it is the directory
name under /XTApps on the card. The result is copied to the card as it is (`tools/xtapp.py install
CARD OUT_DIR` for an image, or a plain copy to /XTApps/<app_id>/ over the reader's USB mode).

THE STORY FILE
    title: The Keeper of Gull Rock        header lines, until the first passage
    app_id: gull-rock
    start: landing

    == landing                            a passage and its id
    @image storm                          its plate (a SCENES name), drawn above the text
    @set read_log                         a flag set on entering the passage
    Text. A blank line between paragraphs.
    @end The Light Holds                  the passage is an ending, with this title; the
    The ship was saved.                   paragraph after @end is the ending's epigraph
    * Climb the tower -> stair            a choice
    * Follow the song -> hymn ? read_log  shown only when the flag is set (`? !flag`: unset)

    Every passage needs at least one visible choice; going to `start` clears the flags.

WHY IT IS ALL ONE index.lua (docs/lua-apps.md, the 7.6.8 probe of 2026-10-12)
    A folder app cannot read its own files (`ctx.data` finds nothing) or `require` modules, so the
    story and the art are string literals in the script. 200 KB of source loaded exactly; 700 KB
    failed on the host's 1 MB Lua heap; this tool refuses past MAX_SOURCE.
    A frame keeps only its first 1024 drawing commands and drops the rest silently, while a layer
    (`ctx.layers:create`) has no cap -- so the engine paints each plate once into a layer and blits
    it with one command. Text drawn into a layer does not appear, so text goes on the frame.

THE ART ENCODING (`ART[name].d`, decoded by the engine's `paint`)
    Per logical row, (gap, run) pairs: skip `gap` white pixels, then `run` black ones; '!' ends the
    row. A value v < 91 is the single character chr(35 + v); larger values are '~' followed by two
    such characters (v // 91, v % 91). The engine draws each logical run as `s` screen lines.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from PIL import Image, ImageDraw, ImageFont      # noqa: E402
import xtapp                                     # noqa: E402

ENGINE = os.path.join(HERE, 'gamebook_engine.lua')
LETTERING_FONT = os.path.join(os.path.dirname(HERE), 'fonts', 'libre-baskerville',
                              'LibreBaskerville-variable.ttf')
MAX_SOURCE = 200_000

# Layout, in the 480 x 800 portrait frame; the engine carries the same numbers.
BODY_W = 424            # text column (x 28 .. 452)
CHOICE_W = 392          # text inside a choice box (box 424 wide, 16 px padding)
PLATE_SCALE = 3         # 160 x 100 logical -> 480 x 300

# The stock's system font as the Lua host draws it (docs/lua-apps.md §4b, measured 2026-09-07).
ADV_BY_WIDTH = {
    4: "'", 5: '.:Iil|', 6: ' ,;`j', 7: '()[]{}', 8: '"\\frt', 9: '*-/1^', 10: '?J_sz',
    11: '7ackvxy~', 12: '!$23459EFLbdeghnopqu', 13: '+068<=>PRSTZ', 14: 'ABCKVXY',
    15: '#&DGHNU', 16: 'OQ', 17: '%@w', 18: 'Mm', 20: 'W',
}
ADV = {c: w for w, chars in ADV_BY_WIDTH.items() for c in chars}


class GamebookError(ValueError):
    pass


# ---------------------------------------------------------------- the story

def parse_story(text, where='story.txt'):
    head, passages, order = {}, {}, []
    cur = None
    para = []

    def flush():
        if para:
            target = cur['epigraph'] if cur.get('ending') and cur['after_end'] else cur['paras']
            target.append(' '.join(para))
            para.clear()

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith('== '):
            if cur is not None:
                flush()
            pid = line[3:].strip()
            if not re.fullmatch(r'[a-z0-9_]+', pid):
                raise GamebookError(f'{where}:{n}: passage id {pid!r}: use a-z, 0-9 and _')
            if pid in passages:
                raise GamebookError(f'{where}:{n}: passage {pid!r} defined twice')
            cur = {'id': pid, 'line': n, 'image': None, 'set': [], 'paras': [], 'choices': [],
                   'ending': None, 'epigraph': [], 'after_end': False}
            passages[pid] = cur
            order.append(pid)
            continue
        if cur is None:
            if not line or line.startswith('#'):
                continue
            m = re.fullmatch(r'([a-z_]+):\s*(.+)', line)
            if not m:
                raise GamebookError(f'{where}:{n}: expected "key: value" before the first passage')
            head[m.group(1)] = m.group(2).strip()
            continue
        if not line:
            flush()
            continue
        if line.startswith('@'):
            flush()
            word, _, arg = line[1:].partition(' ')
            arg = arg.strip()
            if word == 'image':
                cur['image'] = arg
            elif word == 'set':
                cur['set'].append(arg)
            elif word == 'end':
                cur['ending'] = arg
                cur['after_end'] = True
            else:
                raise GamebookError(f'{where}:{n}: unknown directive @{word}')
            continue
        if line.startswith('* '):
            flush()
            m = re.fullmatch(r'\*\s+(.+?)\s+->\s+([a-z0-9_]+)(?:\s+\?\s*(!?)([a-z0-9_]+))?', line)
            if not m:
                raise GamebookError(f'{where}:{n}: a choice is "* Label -> target [? flag | ? !flag]"')
            cur['choices'].append({'label': m.group(1), 'to': m.group(2),
                                   'if': m.group(4), 'not': m.group(3) == '!', 'line': n})
            continue
        para.append(line)
    if cur is not None:
        flush()
    for key in ('title', 'app_id', 'start'):
        if key not in head:
            raise GamebookError(f'{where}: header "{key}:" missing')
    return {'head': head, 'passages': passages, 'order': order}


def validate(story, scenes=None):
    """Hard errors raise; the soft findings come back as a list of warnings."""
    P, head = story['passages'], story['head']
    warnings = []
    if head['start'] not in P:
        raise GamebookError(f'start passage {head["start"]!r} does not exist')
    flags_set = {f for p in P.values() for f in p['set']}
    for p in P.values():
        if not p['choices']:
            raise GamebookError(f'passage {p["id"]!r} (line {p["line"]}) has no choices')
        if not p['paras']:
            raise GamebookError(f'passage {p["id"]!r} (line {p["line"]}) has no text')
        for c in p['choices']:
            if c['to'] not in P:
                raise GamebookError(f'line {c["line"]}: choice target {c["to"]!r} does not exist')
            if c['if'] and c['if'] not in flags_set:
                raise GamebookError(f'line {c["line"]}: flag {c["if"]!r} is tested but never @set')
        if all(c['if'] for c in p['choices']):
            # some combination of flags must leave a choice standing: a flag and its negation
            tested = {(c['if'], c['not']) for c in p['choices']}
            if not any((f, not neg) in tested for f, neg in tested):
                warnings.append(f'passage {p["id"]!r}: every choice is conditional; it can dead-end')
        if scenes is not None and p['image'] and p['image'] not in scenes:
            raise GamebookError(f'passage {p["id"]!r}: @image {p["image"]!r} is not in SCENES')
        for text in p['paras'] + p['epigraph'] + [c['label'] for c in p['choices']] + \
                ([p['ending']] if p['ending'] else []):
            bad = sorted({ch for ch in text if ch not in ADV})
            if bad:
                raise GamebookError(f'passage {p["id"]!r}: characters {"".join(bad)!r} have no measured '
                                    f'width (docs/lua-apps.md §4b covers printable ASCII)')
    seen, todo = set(), [head['start']]
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        todo.extend(c['to'] for c in P[pid]['choices'])
    for pid in story['order']:
        if pid not in seen:
            warnings.append(f'passage {pid!r} is unreachable from {head["start"]!r}')
    if not any(p['ending'] for p in P.values()):
        warnings.append('no passage is an ending (@end)')
    for pid in story['order']:                       # widows: a short last word alone on its line
        for para in P[pid]['paras']:
            lines = wrap(para, BODY_W)
            if len(lines) > 1 and ' ' not in lines[-1] and len(lines[-1]) < 8:
                warnings.append(f'passage {pid!r}: the paragraph ending {lines[-1]!r} leaves it alone '
                                f'on its line')
    return warnings


def textw(s):
    return sum(ADV[c] for c in s)


def wrap(text, width):
    lines, cur = [], ''
    for word in text.split():
        trial = word if not cur else cur + ' ' + word
        if textw(trial) <= width:
            cur = trial
            continue
        if not cur:
            raise GamebookError(f'the word {word!r} is wider than {width} px')
        lines.append(cur)
        cur = word
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- the art

def load_scenes(story_dir):
    path = os.path.join(story_dir, 'scenes.py')
    spec = importlib.util.spec_from_file_location('gamebook_scenes', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SCENES


def lettering(lines, width=480):
    """Display type for the cover and the endings: Libre Baskerville (OFL), thresholded to 1 bit.
    `lines` = [(text, size, weight)], centred; returns a 1-bit image cropped to its ink rows."""
    rows = []
    for text, size, weight in lines:
        f = ImageFont.truetype(LETTERING_FONT, size)
        try:
            f.set_variation_by_axes([weight])
        except (OSError, AttributeError):
            pass
        rows.append((text, f))
    h = sum(int(f.size * 1.45) for _, f in rows) + 8
    img = Image.new('L', (width, h), 255)
    d = ImageDraw.Draw(img)
    y = 4
    for text, f in rows:
        if text == '---':
            d.line([(width * 0.32, y + f.size // 2), (width * 0.68, y + f.size // 2)], fill=0, width=2)
        else:
            w = d.textlength(text, font=f)
            d.text(((width - w) / 2, y), text, font=f, fill=0)
        y += int(f.size * 1.45)
    bw = img.point(lambda v: 0 if v < 140 else 255).convert('1')
    box = Image.eval(bw.convert('L'), lambda v: 255 - v).getbbox()
    if box:
        bw = bw.crop((0, max(0, box[1] - 2), width, min(h, box[3] + 2)))
    return bw


def encode(img):
    """A 1-bit image -> the run string (module docstring)."""
    w, h = img.size
    px = img.load()

    def val(v):
        if v < 91:
            return chr(35 + v)
        if v >= 91 * 91:
            raise GamebookError(f'run value {v} too large')
        return '~' + chr(35 + v // 91) + chr(35 + v % 91)

    out = []
    for y in range(h):
        x = prev = 0
        while x < w:
            if px[x, y] == 0:
                start = x
                while x < w and px[x, y] == 0:
                    x += 1
                out.append(val(start - prev) + val(x - start))
                prev = x
            else:
                x += 1
        out.append('!')
    return ''.join(out)


def decode(data, w, h):
    """The inverse of encode, as the engine does it (used by the tests)."""
    img = Image.new('1', (w, h), 1)
    px = img.load()
    i, y, x, vals = 0, 0, 0, []
    while i < len(data):
        c = data[i]
        if c == '!':
            y, x = y + 1, 0
            i += 1
            continue
        if c == '~':
            v = (ord(data[i + 1]) - 35) * 91 + (ord(data[i + 2]) - 35)
            i += 3
        else:
            v = ord(c) - 35
            i += 1
        vals.append(v)
        if len(vals) == 2:
            gap, run = vals
            vals = []
            x += gap
            for k in range(run):
                px[x + k, y] = 0
            x += run
    return img


def runs_in(img):
    w, h = img.size
    px = img.load()
    return sum(1 for y in range(h) for x in range(w) if px[x, y] == 0 and (x == 0 or px[x - 1, y] != 0))


# ---------------------------------------------------------------- emitting the app

def lua_str(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def long_str(s):
    level = 0
    while (']' + '=' * level + ']') in s:
        level += 1
    eq = '=' * level
    return f'[{eq}[{s}]{eq}]'


def build_art(story, scenes):
    """name -> (1-bit image, scale). Plates at 3x, lettering at 1x."""
    head = story['head']
    art = {}
    for name in sorted({p['image'] for p in story['passages'].values() if p['image']}):
        art[name] = (scenes[name]().ink, PLATE_SCALE)
    if 'ornament' in scenes:          # heads text-only passages and the ending pages (160 x ~10)
        art['_orn'] = (scenes['ornament']().ink, PLATE_SCALE)
    title = head['title']
    pre, _, main = title.rpartition(' of ')
    if pre:
        art['_title'] = (lettering([(pre + ' of', 30, 400), (main.upper(), 50, 700), ('---', 14, 400),
                                    (head.get('subtitle', 'a gamebook'), 22, 400)]), 1)
    else:
        art['_title'] = (lettering([(title, 40, 700), ('---', 14, 400),
                                    (head.get('subtitle', 'a gamebook'), 22, 400)]), 1)
    endings = [p for pid in story['order'] for p in [story['passages'][pid]] if p['ending']]
    for k, p in enumerate(endings, 1):
        art[f'_end{k}'] = (lettering([(p['ending'], 30, 700)]), 1)
    return art, endings


def emit(story, scenes):
    P, head = story['passages'], story['head']
    art, endings = build_art(story, scenes)
    ending_no = {p['id']: k for k, p in enumerate(endings, 1)}
    out = ['-- Generated by tools/gamebook.py from stories/ -- edit the story, not this file.',
           'local STORY = {',
           f'  title = {lua_str(head["title"])},',
           f'  start = {lua_str(head["start"])},',
           f'  endings = {len(endings)},',
           '  passages = {']
    for pid in story['order']:
        p = P[pid]
        lines = []
        for k, para in enumerate(p['paras']):
            if k:
                lines.append('')
            lines.extend(wrap(para, BODY_W))
        fields = [f'lines = {{{", ".join(lua_str(s) for s in lines)}}}']
        if p['image']:
            fields.append(f'img = {lua_str(p["image"])}')
        if p['set']:
            fields.append(f'set = {{{", ".join(lua_str(f) for f in p["set"])}}}')
        if p['ending']:
            epi = [s for para in p['epigraph'] for s in wrap(para, BODY_W)]
            fields.append(f'ending = {ending_no[pid]}')
            fields.append(f'epigraph = {{{", ".join(lua_str(s) for s in epi)}}}')
        ch = []
        for c in p['choices']:
            parts = [f'lines = {{{", ".join(lua_str(s) for s in wrap(c["label"], CHOICE_W))}}}',
                     f'to = {lua_str(c["to"])}']
            if c['if']:
                parts.append(f'flag = {lua_str(c["if"])}')
                if c['not']:
                    parts.append('neg = true')
            ch.append('{' + ', '.join(parts) + '}')
        fields.append(f'choices = {{{", ".join(ch)}}}')
        out.append(f'    {pid} = {{{", ".join(fields)}}},')
    out += ['  },', '}', '', 'local ART = {']
    for name, (img, scale) in art.items():
        w, h = img.size
        out.append(f'  [{lua_str(name)}] = {{w = {w}, h = {h}, s = {scale}, d = {long_str(encode(img))}}},')
    out += ['}', '']
    engine = open(ENGINE).read()
    src = '\n'.join(out) + '\n' + engine
    if len(src.encode()) > MAX_SOURCE:
        raise GamebookError(f'index.lua would be {len(src.encode())} bytes; keep it under {MAX_SOURCE} '
                            f'(the host loaded 200 KB, failed at 700 KB)')
    return src, art


def build(story_dir, out_dir):
    story = parse_story(open(os.path.join(story_dir, 'story.txt'), encoding='utf-8').read(),
                        os.path.join(story_dir, 'story.txt'))
    scenes = load_scenes(story_dir)
    warnings = validate(story, scenes)
    head = story['head']
    if os.path.basename(os.path.abspath(out_dir)) != head['app_id']:
        raise GamebookError(f'{out_dir}: the directory must be named after app_id {head["app_id"]!r} '
                            f'(it becomes /XTApps/{head["app_id"]}/ on the card)')
    src, art = emit(story, scenes)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'index.lua'), 'w') as f:
        f.write(src)
    manifest = {'app_id': head['app_id'], 'display_name': head['title'], 'version': head.get('version', '1.0'),
                'author': head.get('author', 'x4pro-emu'), 'entry': 'index.lua',
                'permissions': ['sys.battery', 'sys.status']}
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    xtapp.pack(out_dir)
    return {'source_bytes': len(src.encode()), 'passages': len(story['passages']),
            'art': {n: {'size': img.size, 'scale': s, 'runs': runs_in(img), 'lines': runs_in(img) * s}
                    for n, (img, s) in art.items()},
            'warnings': warnings}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check'); c.add_argument('story_dir')
    b = sub.add_parser('build'); b.add_argument('story_dir'); b.add_argument('out_dir')
    pl = sub.add_parser('plates'); pl.add_argument('story_dir'); pl.add_argument('out_dir')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'check':
            story = parse_story(open(os.path.join(a.story_dir, 'story.txt'), encoding='utf-8').read())
            warnings = validate(story, load_scenes(a.story_dir))
            for pid in story['order']:
                p = story['passages'][pid]
                tag = f'  [ending: {p["ending"]}]' if p['ending'] else ''
                outs = ', '.join(c['to'] + (f' ?{"!" if c["not"] else ""}{c["if"]}' if c['if'] else '')
                                 for c in p['choices'])
                print(f'{pid:14} -> {outs}{tag}')
            for w in warnings:
                print('warning:', w)
            return 0
        if a.cmd == 'build':
            r = build(a.story_dir, a.out_dir)
            print(f'{a.out_dir}: index.lua {r["source_bytes"]} bytes, {r["passages"]} passages')
            for n, info in r['art'].items():
                print(f'  {n:10} {info["size"][0]}x{info["size"][1]} x{info["scale"]}: '
                      f'{info["runs"]} runs, {info["lines"]} lines painted into its layer')
            for w in r['warnings']:
                print('warning:', w)
            return 0
        if a.cmd == 'plates':
            story = parse_story(open(os.path.join(a.story_dir, 'story.txt'), encoding='utf-8').read())
            art, _ = build_art(story, load_scenes(a.story_dir))
            os.makedirs(a.out_dir, exist_ok=True)
            for n, (img, s) in art.items():
                p = os.path.join(a.out_dir, f'{n.lstrip("_")}.png')
                img.resize((img.size[0] * s, img.size[1] * s), Image.NEAREST).save(p)
                print(p)
            return 0
    except (GamebookError, xtapp.XtappError, OSError) as e:
        print(f'gamebook: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
