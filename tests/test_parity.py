"""tools/parity.py: the device-parity harness for the stock firmware (docs/parity.md).

The fast half needs nothing but Python and Pillow: the step script loads and its coordinates are the
ones the rest of the tree uses, the difference classifier counts every pixel once in the right
region, the panel/portrait frames line up with the .xic convention, the capture alignment pairs and
skips as documented, `compare` passes, fails and is inconclusive on synthetic captures for the right
reasons, the runbook says how to capture, the parity book is byte-stable, and `prepare-card` /
`collect` behave on a directory standing in for the mounted card.

The emulator half walks parity/stock-7.6.8.json on a redacted, developer-patched scratch copy of the
owner's 7.6.8 dump (images/device/flash-2026-10-12-a.bin) and requires every stock Screen Capture to
equal its panel screenshot outside the masks, and every screen to equal its reference in
parity/stock-7.6.8/. Skips without the dump, the card mirror or mtools.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest
from conftest import ROOT, PY, QEMU, instance_name

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import parity                                                                   # noqa: E402
import xic2png                                                                  # noqa: E402

SCRIPT = os.path.join(ROOT, 'parity', 'stock-7.6.8.json')
DUMP_768 = os.path.join(ROOT, 'images', 'device', 'flash-2026-10-12-a.bin')
MIRROR = os.path.join(ROOT, 'images', 'device', 'sd-files')


def tool(*args, check=None):
    r = subprocess.run([PY, os.path.join(ROOT, 'tools', 'parity.py'), *map(str, args)],
                       capture_output=True, text=True, timeout=900)
    if check is not None:
        assert r.returncode == check, f'parity.py {args} exited {r.returncode}, wanted {check}:\n{r.stdout}\n{r.stderr}'
    return r


def blank(fill=255):
    from PIL import Image
    return Image.new('L', (parity.W, parity.H), fill)


def ink(im, box, v=0):
    im = im.copy()
    im.paste(v, box)
    return im


# ---------------------------------------------------------------- the script

def test_the_768_script_loads_with_the_coordinates_the_tree_uses():
    sc = parity.load_script(SCRIPT)
    ids = [s['id'] for s in sc['screens']]
    assert ids == ['home', 'light', 'menu', 'settings', 'all-files', 'reader', 'mini-apps', 'gull-rock']
    assert [s['id'] for s in parity.captured_screens(sc)] == [i for i in ids if i != 'light']
    # portrait -> landscape: the hamburger, Mini Apps and the capture button where the other tests
    # (test_stock.py, test_gamebook.py, test_stock_capture.py) tap them in landscape pixels
    steps = {s['id']: s['steps'] for s in sc['screens']}
    assert parity.landscape(*steps['menu'][0]['at']) == (88, 38)
    assert parity.landscape(*steps['mini-apps'][2]['at']) == (498, 150)
    assert parity.landscape(*sc['capture']['button']) == (733, 199)
    pull = sc['capture']['pull']
    assert parity.landscape(*pull['from']) + parity.landscape(*pull['to']) == (5, 240, 300, 240)
    # the status bar mask is test_stock.STATUS_BAR_COLS (landscape columns 0..59) in portrait rows
    assert sc['masks']['status-bar']['box'] == [0, 0, 480, 60]
    # every state region names a precondition, and the scope-all ones are the global ones
    assert {n for n, p in sc['preconditions'].items() if p.get('scope') == 'all'} == {'system-font', 'language'}
    for s in sc['screens']:
        assert os.path.exists(parity.reference_path(sc, s)), f'no reference for {s["id"]}'


@pytest.mark.parametrize('patch, msg', [
    (lambda sc: sc['masks']['status-bar'].update(box=[0, 0, 481, 60]), 'a box is'),
    (lambda sc: sc['screens'][0]['masks'].append('nope'), 'unknown mask'),
    (lambda sc: sc['screens'][2]['steps'][0].pop('say'), 'needs "say"'),
    (lambda sc: sc['screens'][2]['steps'][0].update(at=[480, 10]), 'a point is'),
    (lambda sc: sc['screens'][0]['state'][0].update(depends='weather'), '"depends" must name'),
    (lambda sc: sc['screens'][3].update(id='home'), 'unique "id"'),
    (lambda sc: sc['preconditions']['bookshelf']['check'].update(kind='guess'), 'check.kind'),
    (lambda sc: sc.update(format='x4pro-parity/0'), '"format"'),
])
def test_script_refusals(tmp_path, patch, msg):
    sc = json.load(open(SCRIPT))
    patch(sc)
    p = tmp_path / 'bad.json'
    p.write_text(json.dumps(sc))
    with pytest.raises(parity.ParityError, match=msg):
        parity.load_script(str(p))


# ---------------------------------------------------------------- frames and differences

def test_a_panel_shot_turns_upright_like_a_capture(tmp_path):
    """portrait (px, py) is landscape (py, 479 - px) (docs/xic.md); a .xic decodes upright, and a
    panel PNG made from it the documented way (ROTATE_90) loads back as the same portrait frame."""
    from PIL import Image
    up = ink(blank(), (10, 20, 11, 21))                                   # one black pixel at (10, 20)
    xic = tmp_path / 'c.xic'
    xic.write_bytes(xic2png.encode_xic(up.convert('1')))
    panel = up.transpose(Image.Transpose.ROTATE_90)
    assert panel.size == (800, 480) and panel.getpixel(parity.landscape(10, 20)) == 0
    panel.save(tmp_path / 'p.png')
    a, b = parity.load_frame(str(xic)), parity.load_frame(str(tmp_path / 'p.png'))
    assert a.getpixel((10, 20)) == 0 and b.getpixel((10, 20)) == 0
    assert parity.classify(a, b)['total'] == 0
    with pytest.raises(parity.ParityError, match='neither'):
        parity.portrait(Image.new('L', (100, 100)))


def test_classify_counts_every_pixel_once_in_the_first_region():
    a = blank()
    b = ink(a, (5, 5, 15, 15))                                 # 100 px in the mask (and in the state box)
    b = ink(b, (100, 300, 110, 310))                           # 100 px in the state region only
    b = ink(b, (400, 700, 402, 705))                           # 10 px nowhere
    masks = [('clock', (0, 0, 60, 60))]
    states = [('bookshelf', (0, 0, 480, 600))]
    held = parity.classify(a, b, masks, states)
    assert held['total'] == 210
    assert held['masked'] == {'clock': 100} and held['state'] == {'bookshelf': 100}
    assert held['unexplained'] == 110 and held['state_explained'] == 0
    assert held['bbox'] == [100, 300, 402, 705]
    unmet = parity.classify(a, b, masks, states, unmet={'bookshelf'})
    assert unmet['unexplained'] == 10 and unmet['state_explained'] == 100
    assert unmet['bbox'] == [400, 700, 402, 705]
    same = parity.classify(a, a, masks, states)
    assert same['total'] == same['unexplained'] == 0 and same['bbox'] is None
    # grey noise under the threshold is not a difference (as `x4emu screenshot --diff`)
    assert parity.classify(a, blank(255 - parity.DIFF_THRESHOLD))['total'] == 0
    assert parity.classify(a, blank(255 - parity.DIFF_THRESHOLD - 1))['total'] == parity.W * parity.H
    pic = parity.diff_picture(a, b, unmet, masks, states)
    assert pic.getpixel((401, 701)) == (230, 20, 20)           # unexplained: red
    assert pic.getpixel((105, 305)) == (230, 160, 0)           # explained by the unmet bookshelf: amber
    assert pic.getpixel((10, 10)) == (40, 90, 230)             # masked: blue


def test_a_saved_game_changes_only_the_covers_state_region():
    """parity/stock-7.6.8/variants/gull-rock-saved-game.png is the cover as the stock captured it in the
    emulator after Begin and Exit (Continue / Begin again, /XTApps/gull-rock/state.xtsv written) -- what
    the owner's reader shows since 2026-10-12. Against the reference cover every differing pixel lies
    in the gamebook-state region, so the plate and the title stay strict."""
    sc = parity.load_script(SCRIPT)
    cover = next(s for s in sc['screens'] if s['id'] == 'gull-rock')
    states = [(r['depends'], tuple(r['box'])) for r in cover['state']]
    saved = parity.load_frame(os.path.join(ROOT, 'parity', 'stock-7.6.8', 'variants', 'gull-rock-saved-game.png'))
    ref = parity.load_frame(parity.reference_path(sc, cover))
    held = parity.classify(saved, ref, [], states)
    assert held['total'] > 1000 and held['state'] == {'gamebook-state': held['total'], 'gamebook': 0}
    assert parity.classify(saved, ref, [], states, unmet={'gamebook-state'})['unexplained'] == 0


def test_align_pairs_in_order_and_skips_extra_and_missing_captures():
    # three screens, four captures, the second capture a stray: it is skipped
    cost = [[0.0, 0.5, 0.6, 0.6],
            [0.6, 0.5, 0.0, 0.7],
            [0.6, 0.4, 0.7, 0.0]]
    assert parity.align(cost) == [0, 2, 3]
    # three screens, two captures (the middle one forgotten): that screen gets none
    assert parity.align([[0.0, 0.6], [0.5, 0.5], [0.6, 0.0]], skip_screen=0.25) == [0, None, 1]


# ---------------------------------------------------------------- compare on synthetic sessions

MINI = {
    'format': parity.FORMAT, 'name': 'mini',
    'firmware': {'stock_version': '7.6.8', 'dump': 'none', 'patches': []},
    'card': {'mirror': 'none'},
    'capture': {'pull': {'from': [240, 5], 'to': [240, 300]}, 'button': [280, 733], 'say': ['capture']},
    'masks': {'clock': {'box': [0, 0, 480, 60], 'why': 'clock'}},
    'preconditions': {
        'shelf': {'check': {'kind': 'absent', 'path': 'XTData/reading/recent.xtbr'}, 'say': 'empty shelf'},
        'font': {'check': {'kind': 'owner'}, 'scope': 'all', 'say': 'same font?'}},
    'screens': [
        {'id': 'one', 'title': 'One', 'masks': ['clock'], 'state': [{'box': [0, 100, 480, 800], 'depends': 'shelf'}]},
        {'id': 'peek', 'title': 'Peek', 'capture': False, 'steps': [{'do': 'note', 'say': 'look'}]},
        {'id': 'two', 'title': 'Two', 'masks': ['clock']},
        {'id': 'three', 'title': 'Three'},
    ]}


def frames():
    """Three distinct screens: a bar at a different height on each."""
    return [ink(blank(), (40, 200 + 150 * k, 440, 260 + 150 * k)) for k in range(3)]


def session(tmp_path, device_frames, precheck=None, names=None):
    """A fake emu/ (emu.json + .xic + panel PNGs) and device/ directory for MINI."""
    script = tmp_path / 'mini.json'
    script.write_text(json.dumps(MINI))
    emu, dev = tmp_path / 'emu', tmp_path / 'device'
    emu.mkdir(exist_ok=True); dev.mkdir(exist_ok=True)
    entries, k = [], 0
    for i, s in enumerate(MINI['screens'], 1):
        e = {'index': i, 'id': s['id'], 'title': s['title'], 'capture': s.get('capture', True),
             'screen_png': f'{i:02d}-{s["id"]}.png'}
        im = frames()[k] if e['capture'] else blank(200)
        im.save(emu / e['screen_png'])
        if e['capture']:
            e['xic'] = f'{i:02d}-{s["id"]}.xic'
            (emu / e['xic']).write_bytes(xic2png.encode_xic(im.convert('1')))
            k += 1
        entries.append(e)
    sha = parity.load_script(str(script))['_sha256']
    (emu / 'emu.json').write_text(json.dumps({'script_sha256': sha, 'screens': entries, 'ok': True}))
    for f in dev.iterdir():
        f.unlink()
    for j, im in enumerate(device_frames):
        n = (names or [])[j] if names else f'screenshot_20261013_1000{j:02d}.xic'
        (dev / n).write_bytes(xic2png.encode_xic(im.convert('1')))
    if precheck is not None:
        (dev / 'precheck.json').write_text(json.dumps(precheck))
    return script, dev, emu


def report(emu):
    return json.load(open(emu.parent / 'report' / 'report.json'))


def test_compare_passes_when_every_difference_is_masked(tmp_path):
    f = frames()
    dev = [ink(f[0], (10, 10, 50, 40)), f[1], f[2]]           # the clock differs on the first, the rest same
    script, d, e = session(tmp_path, dev)
    tool('compare', script, d, e, check=0)
    rep = report(e)
    assert rep['status'] == 'pass' and rep['pairing'] == 'order'
    v = {r['id']: r for r in rep['screens']}
    assert v['one']['verdict'] == 'explained' and v['one']['masked'] == {'clock': 1200}
    assert v['two']['verdict'] == v['three']['verdict'] == 'identical'
    assert v['peek']['verdict'] == 'not captured'
    for n in ('report.md', 'report.html', '01-one-device.png', '01-one-diff.png', '04-three-emu.png'):
        assert (e.parent / 'report' / n).exists(), n
    assert 'clock' in (e.parent / 'report' / 'report.md').read_text()


def test_compare_fails_on_an_unexplained_pixel_and_says_where(tmp_path):
    f = frames()
    script, d, e = session(tmp_path, [f[0], ink(f[1], (300, 600, 305, 602)), f[2]])
    r = tool('compare', script, d, e, check=1)
    assert 'FAIL' in r.stdout
    two = next(x for x in report(e)['screens'] if x['id'] == 'two')
    assert two['verdict'] == 'DIFFERS' and two['unexplained'] == 10 and two['bbox'] == [300, 600, 305, 602]


def test_a_leftover_toast_is_named(tmp_path):
    f = frames()
    MINI['capture']['toast_box'] = [140, 72, 340, 116]
    try:
        script, d, e = session(tmp_path, [f[0], f[1], ink(f[2], (150, 80, 330, 110))])
        tool('compare', script, d, e, check=1)
    finally:
        del MINI['capture']['toast_box']
    three = report(e)['screens'][-1]
    assert three['verdict'] == 'DIFFERS' and 'toast' in three['hint']
    assert 'hint' not in report(e)['screens'][0]


def test_a_state_region_is_explained_only_by_an_unmet_precondition(tmp_path):
    f = frames()
    shelf = ink(f[0], (100, 650, 380, 700))                     # books on the device's bookshelf
    script, d, e = session(tmp_path, [shelf, f[1], f[2]])
    tool('compare', script, d, e, check=1)                      # no precheck: the region is strict
    one = report(e)['screens'][0]
    assert one['verdict'] == 'DIFFERS' and one['state'] == {'shelf': 280 * 50}
    pre = {'preconditions': {'shelf': {'met': False, 'evidence': '/XTData/reading/recent.xtbr exists'}},
           'existing_captures': []}
    script, d, e = session(tmp_path, [shelf, f[1], f[2]], precheck=pre)
    tool('compare', script, d, e, check=0)
    one = report(e)['screens'][0]
    assert one['verdict'] == 'explained' and one['state_explained'] == 280 * 50
    assert any('recent.xtbr exists' in x for x in one['explained'])
    tool('compare', script, d, e, '--precheck', tmp_path / 'none.json', '--not-met', 'shelf', check=0)


def test_a_global_precondition_makes_the_comparison_inconclusive(tmp_path):
    f = frames()
    script, d, e = session(tmp_path, f)
    r = tool('compare', script, d, e, '--not-met', 'font', check=2)
    assert 'INCONCLUSIVE' in r.stdout and report(e)['status'] == 'inconclusive'


def test_old_extra_and_missing_captures(tmp_path):
    f = frames()
    names = ['screenshot_20261001_090000.xic', 'screenshot_20261013_100000.xic',
             'screenshot_20261013_100005.xic', 'screenshot_20261013_100010.xic',
             'screenshot_20261013_100020.xic']
    stray = ink(blank(), (0, 700, 480, 800))
    # an old capture (listed in precheck.json), the three screens, and a stray one in between
    pre = {'preconditions': {}, 'existing_captures': [names[0]]}
    script, d, e = session(tmp_path, [stray, f[0], stray, f[1], f[2]], precheck=pre, names=names)
    tool('compare', script, d, e, check=0)
    rep = report(e)
    assert rep['pairing'] == 'content' and rep['device_captures'] == names[1:]
    assert [r.get('device_xic') for r in rep['screens'] if r['capture']] == [names[1], names[3], names[4]]
    assert any('skipped' in n for n in rep['notes'])
    # the second screen's capture forgotten: it is missing, the others still pair
    script, d, e = session(tmp_path, [f[0], f[2]])
    tool('compare', script, d, e, check=1)
    assert [r['verdict'] for r in report(e)['screens'] if r['capture']] == ['identical', 'missing', 'identical']
    # in order but swapped: paired by order, and the note says the content says otherwise
    script, d, e = session(tmp_path, [f[0], f[2], f[1]])
    tool('compare', script, d, e, check=1)
    assert any('looks more like' in n for n in report(e)['notes'])
    # --since drops captures named before it
    script, d, e = session(tmp_path, [stray, f[0], f[1], f[2]], names=names[:1] + names[2:])
    tool('compare', script, d, e, '--since', '20261013_000000', check=0)


def test_compare_refuses_a_session_without_captures(tmp_path):
    script, d, e = session(tmp_path, [])
    r = tool('compare', script, d, e, check=2)
    assert 'no device captures' in r.stderr


# ---------------------------------------------------------------- runbook, book, card

def test_the_runbook_numbers_the_walk_and_says_how_to_capture():
    r = tool('runbook', SCRIPT, check=0)
    text = r.stdout
    assert 'How to take a Screen Capture' in text and '"Screen Ca"' in text
    assert 'Take a Screen Capture (capture 7 of 7), then count to five.' in text and 'capture 8' not in text
    assert 'three boxed buttons' in text                       # the check that the patch is on the reader
    assert 'do NOT tap Screen Capture' in text                 # the light panel has no capture
    assert 'prepare-card' in text and 'collect' in text
    for q in ('Unbound', 'Reading settings', 'English'):        # the owner's questions
        assert q in text
    order = [text.index(t) for t in ('**Home', '**The light panel', '**The nav menu', '**Settings',
                                     '**All Files', '**The parity book', '**Mini Apps', '**The Keeper')]
    assert order == sorted(order)


def test_the_parity_book_is_byte_for_byte_stable(tmp_path):
    import zipfile
    a, b = tmp_path / 'a.epub', tmp_path / 'b.epub'
    assert parity.make_book(str(a)) == parity.make_book(str(b))
    assert a.read_bytes() == b.read_bytes()
    with zipfile.ZipFile(a) as z:
        first = z.infolist()[0]
        assert first.filename == 'mimetype' and first.compress_type == zipfile.ZIP_STORED
        assert b'Parity Book' in z.read('OEBPS/content.opf')
    assert a.stat().st_size // 1024 == 2                         # All Files rounds it to "3KB"


def fake_card(tmp_path):
    card = tmp_path / 'NO NAME'
    (card / 'XTData' / 'system_fonts').mkdir(parents=True)
    shutil.copytree(os.path.join(ROOT, 'apps', 'gull-rock'), card / 'XTApps' / 'gull-rock')
    (card / 'screenshots').mkdir()
    (card / 'screenshots' / 'screenshot_20261001_090000.xic').write_bytes(b'old')
    (card / 'screenshots' / 'screenshot-3824.bmp').write_bytes(b'bmp')
    sel = os.path.join(MIRROR, 'XTData', 'system_fonts', 'selection.config')
    if os.path.exists(sel):
        shutil.copyfile(sel, card / 'XTData' / 'system_fonts' / 'selection.config')
    return card


def test_prepare_card_plans_writes_and_checks_preconditions(tmp_path):
    card = fake_card(tmp_path)
    (card / 'XTApps' / 'gull-rock' / 'state.xtsv').write_bytes(b'saved game')
    sess = tmp_path / 'session'
    r = tool('prepare-card', SCRIPT, card, '--session', sess, check=1)        # plan only
    assert 'pass --yes' in r.stdout and not (card / 'Parity').exists()
    pre = json.load(open(sess / 'device' / 'precheck.json'))
    assert pre['pending'] == ['Parity/Parity Book.epub']
    assert pre['existing_captures'] == ['screenshot_20261001_090000.xic']
    p = pre['preconditions']
    assert p['bookshelf']['met'] is True and p['apps']['met'] is True and p['gamebook']['met'] is True
    assert p['gamebook-state']['met'] is False                                 # a saved game
    assert p['language']['met'] is None                                        # the owner's question
    if os.path.isdir(MIRROR):
        assert p['system-font']['met'] is True
    tool('prepare-card', SCRIPT, card, '--session', sess, '--yes', check=0)
    book = card / 'Parity' / 'Parity Book.epub'
    want = parity.make_book(str(tmp_path / 'ref.epub'))
    import hashlib
    assert hashlib.sha256(book.read_bytes()).hexdigest() == want
    assert sorted(os.listdir(card / 'Parity')) == ['Parity Book.epub']         # no ._ file
    r = tool('prepare-card', SCRIPT, card, '--session', sess, check=0)        # idempotent
    assert 'already there, identical' in r.stdout
    # not the reader's card: nothing read or written
    other = tmp_path / 'USBSTICK'; other.mkdir()
    r = tool('prepare-card', SCRIPT, other, '--session', sess, '--yes', check=2)
    assert 'does not look like' in r.stderr and not (other / 'Parity').exists()


def test_collect_copies_only_the_sessions_captures(tmp_path):
    card = fake_card(tmp_path)
    sess = tmp_path / 'session'
    tool('prepare-card', SCRIPT, card, '--session', sess, check=1)
    for t in ('100000', '100030'):
        (card / 'screenshots' / f'screenshot_20261013_{t}.xic').write_bytes(b'new')
    r = tool('collect', SCRIPT, card, '--session', sess, check=0)
    got = sorted(n for n in os.listdir(sess / 'device') if n.endswith('.xic'))
    assert got == ['screenshot_20261013_100000.xic', 'screenshot_20261013_100030.xic']
    assert '2 new capture(s), the script takes 7' in r.stdout


# ---------------------------------------------------------------- the emulator walk

@pytest.fixture
def walk_prereqs():
    for path, what in ((DUMP_768, 'the 7.6.8 device dump'), (MIRROR, 'images/device/sd-files'),
                       (QEMU, 'qemu')):
        if not os.path.exists(path):
            pytest.skip(f'{what} not available')
    if not shutil.which('mcopy'):
        pytest.skip('mtools not available')


def test_emulator_walk_captures_equal_their_panels_on_stock_768(walk_prereqs, tmp_path, request):
    """The whole emulator side: build the redacted, developer-patched copy and the card, walk the
    eight screens, and every .xic the stock wrote equals the panel screenshot outside the masks; each
    screen equals its reference in parity/stock-7.6.8/ (else the walk stops with the screen it
    reached). Then the emulator's captures, renamed as the reader names them, compare clean against
    themselves through `compare`, which is the device session's last step."""
    name = instance_name(request.node.name, request.node.nodeid)
    sess = tmp_path / 's'
    r = tool('emu', SCRIPT, '--session', sess, '--name', name, '--work', tmp_path)
    assert r.returncode == 0, f'{r.stdout}\n{r.stderr}'
    man = json.load(open(sess / 'emu' / 'emu.json'))
    assert man['ok'] and man['epd_unknown_cmds'] == 0
    caps = [e for e in man['screens'] if e['capture']]
    assert len(caps) == 7
    for e in caps:
        assert e['check']['xic_bytes'] == parity.CAPTURE_BYTES, e
        assert e['check']['vs_screen']['unexplained'] == 0, e
    assert all(e['reference'] == {'unexplained': 0, 'masked': e['reference']['masked']} for e in man['screens'])
    assert not (sess / 'emu' / 'flash.bin').exists()                   # the scratch copy is gone
    dev = sess / 'device'
    dev.mkdir()
    for e in caps:
        shutil.copyfile(sess / 'emu' / e['xic'], dev / e['card_name'])
    tool('compare', SCRIPT, dev, sess / 'emu', check=0)
    assert all(r['verdict'] in ('identical', 'not captured') for r in report(sess / 'emu')['screens'])
