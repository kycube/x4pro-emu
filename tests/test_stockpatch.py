"""tools/stockpatch.py on a scratch copy of the stock app -- no emulator, no QEMU, ~5 s.

`stockpatch.py` is the patch manifest: three named byte patches on `xteink_app` 7.2.4, each
`{name, description, app_offset, original, patched}`, applied and reverted through the same ESP
image arithmetic `tools/stockdev.py` uses (imported, not copied). What this file proves:

  * every patch site holds the bytes the manifest says it holds, in the real image,
  * `apply` changes ONLY those bytes plus the checksum byte and the 32-byte SHA-256, and
    `esptool image-info` -- an independent implementation of the same arithmetic -- calls both valid,
  * `revert` puts the image back byte for byte, so a scratch image can be re-used,
  * `developer-menu` produces exactly the bytes `tools/stockdev.py` produces (the old tool and the
    new manifest are the same patch),
  * the same offsets land at +0x10000 in a 16 MB flash image,
  * anything that is not stock 7.2.4 -- another project, another version, altered bytes at a site --
    is refused instead of being corrupted at a guessed offset.

Needs images/device/stock-app0-7.2.4.bin (gitignored, extracted from the device dump); skips without
it, as the stock tests do on CI. Everything happens on a copy in pytest's tmp_path.
"""
import os, subprocess, sys
import pytest
from conftest import ROOT, PY

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import stockpatch                                                              # noqa: E402
import stockdev                                                                # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'stockpatch.py')
STOCKDEV = os.path.join(ROOT, 'tools', 'stockdev.py')
APP = os.path.join(ROOT, 'images', 'device', 'stock-app0-7.2.4.bin')
CROSSPOINT = os.path.join(ROOT, 'firmware', '.pio', 'build', 'x4pro', 'firmware.bin')
APP_BYTES = 5503680
CHECKSUM_OFF = 0x53fa9f          # last byte of the 16-byte block after segment 7
HASH_OFF = 0x53faa0              # ..0x53fabf, the end of the image
FOOTER = {CHECKSUM_OFF} | set(range(HASH_OFF, HASH_OFF + 32))

# the manifest, spelled out here so a silent edit of the tool's table fails this file
EXPECTED = {
    'developer-menu':   (0x4eabe0, '36 41 00 0c 02 1d f0', '36 41 00 0c 12 1d f0'),
    'hidden-menu-rows': (0x4f516a, 'b6 29 07', 'b6 29 ff'),
    'lua-apps-row':     (0x2ed873, '82 02 ac', '82 a0 01'),
}


def site_bytes(name):
    """The file offsets a patch actually rewrites: the bytes of its window that differ (the window
    also carries context -- the whole developer stub -- which stays as it is)."""
    p = stockpatch.PATCHES[name]
    return {p.app_offset + i for i in range(p.size) if p.original[i] != p.patched[i]}


def run(*args, expect=0, tool=TOOL):
    r = subprocess.run([PY, tool, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, \
        f'{os.path.basename(tool)} {args} exited {r.returncode}, wanted {expect}:\n{r.stdout}\n{r.stderr}'
    return r.stdout + r.stderr


def image_info(path):
    """esptool's own view of the image footer (the independent check on our arithmetic)."""
    r = subprocess.run([PY, '-m', 'esptool', '--chip', 'esp32s3', 'image-info', str(path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f'esptool image-info failed:\n{r.stdout}\n{r.stderr}'
    return r.stdout


@pytest.fixture(scope='module')
def original():
    if not os.path.exists(APP):
        pytest.skip('images/device/stock-app0-7.2.4.bin not available')
    return open(APP, 'rb').read()


@pytest.fixture
def app(tmp_path, original):
    """A scratch copy of the stock app image."""
    dst = tmp_path / 'app.bin'
    dst.write_bytes(original)
    return dst


def test_the_manifest_matches_the_real_image(app, original):
    """Every site is where the table says, holding the original bytes -- and `list` says so."""
    assert len(original) == APP_BYTES
    assert set(stockpatch.PATCHES) == set(EXPECTED), 'the patch table changed'
    for name, (off, before, after) in EXPECTED.items():
        p = stockpatch.PATCHES[name]
        assert (p.app_offset, p.original.hex(' '), p.patched.hex(' ')) == (off, before, after), name
        assert original[off:off + p.size] == p.original, f'{name} is not at {off:#x} in the image'
        assert p.description and p.detail and p.va, f'{name} has no description/detail/VA'

    out = run('list', app)
    for name, (off, before, _) in EXPECTED.items():
        assert f'{name}' in out and 'original' in out and before in out, out
    assert 'bare app image' in out and 'checksum valid' in out and 'hash valid' in out, out
    assert run('list', app, '--check', expect=1)                # not all applied
    assert run(app, 'list') == out, '`IMAGE list` must be the same as `list IMAGE`'
    assert open(app, 'rb').read() == original, 'list wrote to the image'


@pytest.mark.parametrize('name', list(EXPECTED))
def test_each_patch_changes_only_its_bytes_and_the_footer(app, original, name):
    """One patch at a time: its own bytes, the checksum byte and the hash -- nothing else in 5.5 MB,
    and esptool agrees about the footer it did not write."""
    off, before, after = EXPECTED[name]
    out = run('apply', app, name)
    assert f'{before} -> {after}' in out and f'{off:#x}' in out, out
    got = open(app, 'rb').read()
    changed = {i for i in range(len(got)) if got[i] != original[i]}
    want = site_bytes(name) | FOOTER
    assert changed == want, f'{name} changed {sorted(changed ^ want)[:20]} unexpectedly'

    info = image_info(app)
    for field in ('Checksum', 'Validation hash'):
        assert '(valid)' in info.split(field)[1].split('\n')[0], info
    assert 'App version: 7.2.4' in info, 'the patch must not disturb the app description'
    assert stockdev.verify_image(bytearray(got)) == (True, True)

    assert run('list', app).count('applied') == 1
    assert run('apply', app, name, '--check') == run('apply', app, name, '--check')   # exit 0: done
    run('revert', app, name)
    assert open(app, 'rb').read() == original, f'revert did not undo {name} byte for byte'


def test_apply_all_then_revert_all_round_trips(app, original):
    """`all` names every patch; the image comes back byte-identical, so a scratch copy is reusable."""
    out = run('apply', app, 'all')
    for name in EXPECTED:
        assert name in out, out
    got = open(app, 'rb').read()
    changed = {i for i in range(len(got)) if got[i] != original[i]}
    want = FOOTER.union(*(site_bytes(n) for n in EXPECTED))
    assert changed == want, sorted(changed - want)[:20]
    assert run('list', app, '--check') and run('list', app).count('applied') == 3
    assert '(valid)' in image_info(app).split('Validation hash')[1].split('\n')[0]

    out = run('apply', app, 'all')                       # idempotent
    assert out.count('already applied') == 3, out
    assert open(app, 'rb').read() == got

    run('revert', app, 'all')
    assert open(app, 'rb').read() == original
    assert run('revert', app, 'all', '--check')          # exit 0: already original


def test_developer_menu_is_the_same_patch_as_stockdev(app, tmp_path, original):
    """The old one-command tool and the new manifest must produce the same image, or `--check` of
    one would disagree with the other on a file the other wrote."""
    other = tmp_path / 'stockdev.bin'
    other.write_bytes(original)
    run('apply', app, 'developer-menu')
    run(other, tool=STOCKDEV)
    assert open(app, 'rb').read() == other.read_bytes()
    assert 'developer mode: patched' in run(app, '--check', tool=STOCKDEV)
    assert stockdev.stub_state(open(app, 'rb').read()) == 'patched'


def test_the_offsets_move_by_0x10000_in_a_flash_image(app, tmp_path, original):
    """A 16 MB flash image is detected by its size and the 0xE9 at 0x10000; its app0 slot comes out
    identical to the patched bare app, and nothing outside the slot is touched."""
    flash = tmp_path / 'flash.bin'
    blob = bytearray(b'\xff' * stockdev.FLASH_SIZE)
    blob[0x10000:0x10000 + APP_BYTES] = original
    flash.write_bytes(blob)

    out = run('list', flash)
    assert 'app0 at 0x10000' in out and f'{0x10000 + 0x2ed873:#09x}' in out, out
    out = run('apply', flash, 'lua-apps-row')
    assert f'{0x10000 + 0x2ed873:#x}' in out and 'WiFi credentials' in out, out

    run('apply', app, 'lua-apps-row')
    patched = bytearray(flash.read_bytes())
    assert bytes(patched[0x10000:0x10000 + APP_BYTES]) == open(app, 'rb').read()
    assert bytes(patched[:0x10000]) == b'\xff' * 0x10000, 'nothing outside the app slot changed'
    assert stockdev.verify_image(patched, 0x10000) == (True, True)


def test_anything_that_is_not_stock_7_2_4_is_refused(app, tmp_path, original):
    """Another build puts different code at these offsets, so writing there would corrupt an
    instruction at random: refuse (exit 2) and say how to find the sites again."""
    altered = tmp_path / 'altered.bin'
    blob = bytearray(original)
    blob[0x2ed873] = 0x37                                # the site is there, the code is not
    altered.write_bytes(blob)
    out = run('apply', altered, 'lua-apps-row', expect=2)
    assert 'lua-apps-row' in out and '82 02 ac' in out and '82 a0 01' in out, out
    assert altered.read_bytes() == bytes(blob), 'a refused image must not be written'
    assert 'unknown' in run('list', altered)
    assert run('list', altered, '--check', expect=2)
    run('apply', altered, 'developer-menu')              # the other sites still work

    if os.path.exists(CROSSPOINT):                       # a valid ESP32-S3 app, not xteink_app
        out = run('apply', CROSSPOINT, 'all', expect=2)
        assert 'not xteink_app 7.2.4' in out and 'ghidra_stock.py' in out, out

    junk = tmp_path / 'junk.bin'
    junk.write_bytes(b'not an esp image' * 64)
    assert 'no ESP image magic' in run('list', junk, expect=2)
    assert 'unknown patch' in run('apply', app, 'no-such-patch', expect=2)


def test_the_importable_api_matches_the_cli(app, original):
    """`apply()`/`revert()` return the bytes and a report; `states()` names the three cases."""
    assert stockpatch.app_version(original) == ('xteink_app', '7.2.4')
    st = stockpatch.states(original)
    assert {n: r['state'] for n, r in st.items()} == {n: 'original' for n in EXPECTED}
    assert st['lua-apps-row']['offset'] == 0x2ed873 and st['lua-apps-row']['va'] == 0x4213d873

    out, rep = stockpatch.apply(original, ['lua-apps-row', 'developer-menu'])
    assert [c['name'] for c in rep['changes']] == ['developer-menu', 'lua-apps-row']  # table order
    assert all(c['changed'] and c['now'] == 'applied' for c in rep['changes']), rep
    assert rep['checksum_ok'] and rep['hash_ok']
    assert (rep['checksum_offset'], rep['hash_offset']) == (CHECKSUM_OFF, HASH_OFF)
    assert out[HASH_OFF:HASH_OFF + 32].hex() == rep['sha256']
    assert stockpatch.state(out, 'hidden-menu-rows') == 'original'

    run('apply', app, 'developer-menu', 'lua-apps-row')   # the CLI produces the same bytes
    assert open(app, 'rb').read() == out
    back, _ = stockpatch.revert(out, ['all'])
    assert back == original

    with pytest.raises(stockpatch.StockPatchError, match='unknown patch'):
        stockpatch.apply(original, ['nope'])
    with pytest.raises(stockpatch.StockPatchError, match='refusing to apply'):
        stockpatch.apply(original[:0x2ed873] + b'\x37' + original[0x2ed874:], ['lua-apps-row'])
