"""tools/gamebook.py and the gamebook engine (tools/gamebook_engine.lua), with stories/gull-rock.

The fast half needs nothing but Python: the story parser's refusals, the text wrapping against the
host's font widths, the art encoding round trip, and that the committed apps/gull-rock is exactly what
the tool builds from stories/gull-rock today (so the card-ready folder never drifts from its source).

The emulator half boots the owner's stock 7.6.8 (images/device/flash-2026-10-12-a.bin, a redacted
copy) with the app on the device card -- no firmware patch: 7.6.8 shows "Mini Apps" by itself once
/XTApps holds an app -- and plays the logbook branch to its ending: title, a plate page, a choice that
only exists because a flag was set, and the ending page, against goldens. Skips without the dump.
"""
import os
import shutil
import subprocess
import sys

import pytest
from conftest import x4emu, ROOT, PY, instance_name

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import gamebook                                                                 # noqa: E402
import xtapp                                                                    # noqa: E402
from test_stock import (PANEL_W, boot_to_home, masked_diff, settle, state,      # noqa: E402
                        stock_card, tap)                                        # noqa: F401

STORY = os.path.join(ROOT, 'stories', 'gull-rock')
APP = os.path.join(ROOT, 'apps', 'gull-rock')
DUMP_768 = os.path.join(ROOT, 'images', 'device', 'flash-2026-10-12-a.bin')


# ---------------------------------------------------------------- fast

def test_story_parses_and_every_passage_is_reachable():
    story = gamebook.parse_story(open(os.path.join(STORY, 'story.txt')).read())
    warnings = gamebook.validate(story, gamebook.load_scenes(STORY))
    assert warnings == [], warnings
    endings = [p['ending'] for p in story['passages'].values() if p['ending']]
    assert len(endings) == 5 and 'Two Keepers' in endings


@pytest.mark.parametrize('text, msg', [
    ('title: T\napp_id: t\nstart: a\n== a\nHi.\n* Go -> b\n', 'does not exist'),
    ('title: T\napp_id: t\nstart: a\n== a\nHi.\n', 'no choices'),
    ('title: T\napp_id: t\nstart: a\n== a\nHi.\n* Go -> a ? key\n', 'never @set'),
    ('title: T\napp_id: t\nstart: a\n== a\nCafé.\n* Go -> a\n', 'no measured width'),
    ('title: T\nstart: a\n== a\nHi.\n* Go -> a\n', 'app_id'),
])
def test_story_refusals(text, msg):
    with pytest.raises(gamebook.GamebookError, match=msg):
        gamebook.validate(gamebook.parse_story(text))


def test_wrap_respects_the_measured_widths():
    text = ('Above you the tower is a black finger against a blacker sky. Somewhere out in the rain, '
            'a ship is due before midnight.')
    lines = gamebook.wrap(text, gamebook.BODY_W)
    assert ' '.join(lines) == text
    assert all(gamebook.textw(s) <= gamebook.BODY_W for s in lines)
    # greedy: the next word would not have fitted on any line
    for a, b in zip(lines, lines[1:]):
        assert gamebook.textw(a + ' ' + b.split()[0]) > gamebook.BODY_W


def test_art_round_trips_and_fits_the_engine():
    scenes = gamebook.load_scenes(STORY)
    for name in ('storm', 'log', 'ledge'):
        img = scenes[name]().ink
        data = gamebook.encode(img)
        assert '\n' not in data and all(32 < ord(c) < 127 for c in data)
        back = gamebook.decode(data, *img.size)
        assert back.tobytes() == img.tobytes(), name
    # a wide run needs the three-character escape
    from PIL import Image
    wide = Image.new('1', (480, 2), 1)
    for x in range(10, 300):
        wide.putpixel((x, 1), 0)
    d = gamebook.encode(wide)
    assert '~' in d and gamebook.decode(d, 480, 2).tobytes() == wide.tobytes()


def test_the_committed_app_is_what_the_tool_builds(tmp_path):
    out = tmp_path / 'gull-rock'
    rep = gamebook.build(STORY, str(out))
    assert rep['source_bytes'] < gamebook.MAX_SOURCE
    for f in ('index.lua', 'manifest.json', 'app.xtapp'):
        assert (out / f).read_bytes() == open(os.path.join(APP, f), 'rb').read(), \
            f'apps/gull-rock/{f} is stale: run tools/gamebook.py build stories/gull-rock apps/gull-rock'
    src = (out / 'index.lua').read_text()
    assert 'require' not in src                             # the host cannot require (2026-10-12 probe)


def test_luac_accepts_the_app_when_available():
    """A syntax check of the generated script (skips when no Lua compiler is installed)."""
    luac = shutil.which('luac') or shutil.which('luac5.4') or shutil.which('luac5.5')
    if not luac:
        pytest.skip('no luac on PATH')
    r = subprocess.run([luac, '-p', os.path.join(APP, 'index.lua')], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- emulator

HAMBURGER = (88, 38)
MINI_APPS = (500, 150)            # 7.6.8's fifth row once /XTApps holds an app (unpatched)
APP_CARD = (225, 355)


def ptap(name, x, y):
    """A tap in the app's portrait frame (the panel is the landscape screenshot)."""
    return tap(name, y, 479 - x)


def upright(png):
    from PIL import Image
    return Image.open(png).rotate(-90, expand=True)


def choice_boxes(png):
    """(top, bottom) of each choice box on a page: rows black from x 28 to 451, white at 27 and 452."""
    im = upright(png).convert('L').load()
    edges = [y for y in range(800)
             if im[27, y] == 255 and im[452, y] == 255 and all(im[x, y] == 0 for x in range(28, 452))]
    return list(zip(edges[::2], edges[1::2]))


@pytest.fixture
def gamebook_stock(tmp_path, request, stock_card):
    if not os.path.exists(DUMP_768):
        pytest.skip('the 7.6.8 device dump is not available')
    if not shutil.which('mcopy'):
        pytest.skip('mtools not available')
    name = instance_name(request.node.name, request.node.nodeid)
    flash = tmp_path / 'stock.bin'
    shutil.copyfile(DUMP_768, flash)
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'nvsedit.py'), str(flash), 'redact'],
                   check=True, stdout=subprocess.DEVNULL)
    sd = tmp_path / 'sd.img'
    shutil.copyfile(stock_card, sd)
    rep = xtapp.install(str(sd), APP)
    assert rep['card_dir'] == '/XTApps/gull-rock', rep
    x4emu(name, 'stop', check=False)
    x4emu(name, 'run', '--flash', str(flash), '--sd', str(sd), '--boot-hold-power')
    yield name, tmp_path
    x4emu(name, 'stop', check=False)


def shot(name, tmp, label):
    p = tmp / f'{label}.png'
    x4emu(name, 'screenshot', str(p))
    return p


def golden(p, gname):
    g = os.path.join(ROOT, 'tests', 'golden', gname)
    if not os.path.exists(g):
        shutil.copyfile(p, g)
        print(f'golden created: {g}')
    n = masked_diff(p, g, (0, PANEL_W))          # the app owns the whole frame, status bar included
    assert n == 0, f'{p.name} differs from tests/golden/{gname} in {n} pixels'


def choose(name, tmp, label, k):
    p = shot(name, tmp, label)
    boxes = choice_boxes(p)
    assert len(boxes) >= k, f'{label}: {len(boxes)} choice boxes, wanted #{k}'
    a, b = boxes[k - 1]
    ptap(name, 240, (a + b) // 2)
    settle(name, 2, 60)


def open_app(name, tmp, tries=4):
    """Tap the app's card on the Mini Apps list until the gamebook is on screen. The list sometimes
    answers the first tap with a repaint of itself (the card's selection) instead of opening it."""
    for attempt in range(tries):
        tap(name, *APP_CARD)
        settle(name, 3, 60)
        top = upright(shot(name, tmp, f'open{attempt}')).convert('L').crop((0, 0, 480, 300))
        if sum(1 for v in top.tobytes() if v == 0) > 0.3 * 480 * 300:     # the cover plate is mostly black
            return
    raise AssertionError(f'the gamebook did not open after {tries} taps on its card')


def test_gamebook_plays_to_an_ending_on_stock_768(gamebook_stock):
    name, tmp = gamebook_stock
    boot_to_home(name)
    settle(name, 2, 30)
    tap(name, *HAMBURGER)
    settle(name, 2, 30)
    tap(name, *MINI_APPS)
    settle(name, 2, 30)
    open_app(name, tmp)
    golden(shot(name, tmp, 'title'), 'gamebook-title.png')            # plate + lettering via layers

    choose(name, tmp, 'title2', 1)                                     # Begin
    x4emu(name, 'press', 'right', '--quiet', '1', '--wait', '20')      # the side button turns the page
    settle(name, 2, 60)
    choose(name, tmp, 'landing2', 2)                                   # the cottage
    choose(name, tmp, 'cottage', 1)                                    # read the log: sets read_log
    x4emu(name, 'press', 'right', '--quiet', '1', '--wait', '20')
    settle(name, 2, 60)
    choose(name, tmp, 'log2', 1)                                       # the trapdoor
    cave = shot(name, tmp, 'cave')
    assert len(choice_boxes(cave)) == 2, 'with the log read, the cave offers one "follow" choice, not two'
    choose(name, tmp, 'cave2', 1)                                      # follow: the hymn, not the ebb
    golden(shot(name, tmp, 'ledge'), 'gamebook-ledge.png')
    choose(name, tmp, 'ledge2', 1)
    x4emu(name, 'press', 'right', '--quiet', '1', '--wait', '20')
    settle(name, 2, 60)
    golden(shot(name, tmp, 'ending'), 'gamebook-ending.png')           # Two Keepers, centred

    st = state(name)
    assert st['epd_unknown_cmds'] == 0, st
    assert 'Script load failed' not in x4emu(name, 'log').stdout
