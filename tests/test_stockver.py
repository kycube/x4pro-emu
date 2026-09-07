"""tools/stockver.py: the per-version constants of the stock tools -- no emulator, ~2 s.

Every offset `tools/stockdev.py`, `tools/stockpatch.py` and `tools/stockstrings.py` use was found in
one build and means nothing in another, so they no longer carry any: the numbers live in
`tools/stockver.d/<version>.json` and an image picks its own file by what its `esp_app_desc` says.
This file proves the four things that has to guarantee:

  * the real stock app is identified and hands the tools the numbers 7.2.4 always had,
  * an image whose version string no file describes is refused, with a message that names the
    versions that *are* known and where to add one -- never patched at a guessed offset,
  * a known version whose `app_elf_sha256` is not the one its file names is refused too (that hash
    is the identity of a build: our own patches never change it, so a mismatch is another build),
  * every shipped version file validates against the schema, a malformed one is reported cleanly
    instead of crashing the loader, and a file another agent drops in while we run is picked up.

Needs images/device/stock-app0-7.2.4.bin (gitignored) for the image half; the schema half runs
anywhere. Nothing is written outside pytest's tmp_path.
"""
import json, os, shutil, subprocess, sys
import pytest
from conftest import ROOT, PY

sys.path.insert(0, os.path.join(ROOT, 'tools'))
import stockver                                                                 # noqa: E402
import stockdev                                                                 # noqa: E402
import stockpatch                                                               # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'stockver.py')
APP = os.path.join(ROOT, 'images', 'device', 'stock-app0-7.2.4.bin')
VERSION_AT = 0x20 + 0x10         # esp_app_desc: version[32]
ELF_AT = 0x20 + 0x90             # esp_app_desc: app_elf_sha256[32]

# 7.2.4's numbers, spelled out here so a silent edit of tools/stockver.d/7.2.4.json fails this file
V724 = {
    'app_elf_sha256': '5e6f90d4ab9035c188d586df3276ca67bd088e240e58aa1ecb02b8c66fa0f4df',
    'app_sha256': 'c1fed9ff411775bb72aadf51529a707b4d54f2c46e5a6ebdf51dc0f77d8a2fc1',
    'app_bytes': 5503680,
    'dev_stub': (0x4eabe0, '36 41 00 0c 02 1d f0', 4, 0x02, 0x12),
    'patches': {'developer-menu':   (0x4eabe0, '36 41 00 0c 02 1d f0', '36 41 00 0c 12 1d f0'),
                'hidden-menu-rows': (0x4f516a, 'b6 29 07', 'b6 29 ff'),
                'lua-apps-row':     (0x2ed873, '82 02 ac', '82 a0 01')},
    'packs': {'counts': (18, 0x3c3f7d54, 0x3c3f7a08), 'labels': (234, 0x3c3fb988, 0x3c3f7d54),
              'toasts': (688, 0x3c4a0e31, None)},
    'lang_count': 4,
}


def run(*args, expect=0):
    r = subprocess.run([PY, TOOL, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, \
        f'stockver {args} exited {r.returncode}, wanted {expect}:\n{r.stdout}\n{r.stderr}'
    return r.stdout + r.stderr


@pytest.fixture(scope='module')
def original():
    if not os.path.exists(APP):
        pytest.skip('images/device/stock-app0-7.2.4.bin not available')
    return open(APP, 'rb').read()


def edited(tmp_path, original, at, value, name='fake.bin'):
    """A copy of the stock app with `value` written over the descriptor field at `at`."""
    blob = bytearray(original)
    blob[at:at + len(value)] = value
    dst = tmp_path / name
    dst.write_bytes(bytes(blob))
    return dst, bytes(blob)


# ---------------------------------------------------------------- the real image
def test_the_stock_app_is_identified_and_carries_7_2_4s_numbers(original, tmp_path):
    """The selector is (project, version) from `esp_app_desc`; what comes back is the table the
    three tools used to hard-code, and the same table lands at 0x10000 in a flash image."""
    base, ver = stockver.identify(bytearray(original))
    assert (base, ver.project, ver.version) == (0, 'xteink_app', '7.2.4')
    assert ver.app_elf_sha256 == V724['app_elf_sha256'] and ver.app_sha256 == V724['app_sha256']
    assert ver.app_bytes == V724['app_bytes'] == len(original)
    assert os.path.basename(ver.path) == '7.2.4.json'

    stub = ver.need_dev_stub()
    assert (stub.app_offset, stub.stub.hex(' '), stub.movi_index, stub.unpatched, stub.patched) \
        == V724['dev_stub']
    assert stub.movi_offset == 0x4eabe4 and stub.va == 0x4233abe0
    assert original[stub.app_offset:stub.app_offset + stub.size] == stub.stub, 'the stub is there'

    assert set(ver.patches) == set(V724['patches'])
    for name, (off, before, after) in V724['patches'].items():
        p = ver.need_patch(name)
        assert (p.app_offset, p.original.hex(' '), p.patched.hex(' ')) == (off, before, after), name
        assert original[off:off + p.size] == p.original, f'{name} is not at {off:#x}'

    assert set(ver.packs) == set(V724['packs'])
    for name, (groups, limit, header) in V724['packs'].items():
        s = ver.need_pack(name)
        assert (s.groups, s.limit_va, s.header_va) == (groups, limit, header), name
    assert ver.need_lang_count() == V724['lang_count']
    assert ver.known_groups[11] == 'USB Mode' and ver.known_groups[9] == 'Lua Apps'

    flash = bytearray(b'\xff' * stockdev.FLASH_SIZE)                 # the same app in a flash image
    flash[0x10000:0x10000 + len(original)] = original
    assert stockver.identify(flash)[0] == 0x10000
    assert stockver.identify(flash)[1].key == ver.key

    out = run('identify', APP)
    assert 'xteink_app 7.2.4' in out and 'bare app image' in out and '0x4eabe0' in out, out


def test_our_own_patches_do_not_change_the_identity(original):
    """`app_elf_sha256` is the hash of the *ELF*, not of the image: applying every patch (which
    rewrites bytes, the checksum and the appended SHA-256) leaves the image identifiable."""
    patched, _ = stockpatch.apply(original, ['all'])
    assert patched != original
    assert stockver.identify(bytearray(patched))[1].version == '7.2.4'
    assert stockver.app_desc(patched)['elf_sha256'] == V724['app_elf_sha256']


# ---------------------------------------------------------------- refusals
def test_an_unknown_version_is_refused_by_the_loader_and_by_every_tool(original, tmp_path):
    """A version string no file describes: the loader raises UnknownVersion and the message names
    the known versions and where to add one. The three tools exit 2 rather than write."""
    fake, blob = edited(tmp_path, original, VERSION_AT, b'9.9.9' + b'\0' * 27)
    with pytest.raises(stockver.UnknownVersion) as e:
        stockver.identify(bytearray(blob))
    msg = str(e.value)
    assert '9.9.9' in msg and 'xteink_app 7.2.4' in msg, msg
    assert 'tools/stockver.d/' in msg and 'stockver.py' in msg, msg
    assert e.value.version == '9.9.9' and e.value.project == 'xteink_app'

    for tool, args in (('stockdev.py', [fake]), ('stockpatch.py', ['list', fake]),
                       ('stockstrings.py', [fake, 'list'])):
        r = subprocess.run([PY, os.path.join(ROOT, 'tools', tool), *map(str, args)],
                           capture_output=True, text=True)
        assert r.returncode == 2, f'{tool} exited {r.returncode}\n{r.stdout}\n{r.stderr}'
        out = r.stdout + r.stderr
        assert '9.9.9' in out and 'tools/stockver.d/' in out, out
    assert fake.read_bytes() == blob, 'a refused image must not be written'

    junk = bytearray(b'\xe9' + b'\x00' * 4096)                       # ESP magic, no descriptor
    with pytest.raises(stockver.UnknownVersion, match='no esp_app_desc'):
        stockver.identify(junk)


def test_a_tampered_elf_sha_is_refused(original, tmp_path):
    """The same version number on a different build: the ELF hash says so and the tools stop."""
    bad = bytearray(original[ELF_AT:ELF_AT + 32])
    bad[0] ^= 0xff
    img, blob = edited(tmp_path, original, ELF_AT, bytes(bad), 'other-build.bin')
    with pytest.raises(stockver.StockVerError) as e:
        stockver.identify(bytearray(blob))
    msg = str(e.value)
    assert 'app_elf_sha256' in msg and V724['app_elf_sha256'] in msg, msg
    assert not isinstance(e.value, stockver.UnknownVersion), 'the version itself was found'

    with pytest.raises(stockdev.StockDevError, match='app_elf_sha256'):
        stockdev.patch(blob)
    r = subprocess.run([PY, os.path.join(ROOT, 'tools', 'stockpatch.py'), 'apply', str(img), 'all'],
                       capture_output=True, text=True)
    assert r.returncode == 2 and 'app_elf_sha256' in r.stdout + r.stderr
    assert img.read_bytes() == blob, 'a refused image must not be written'


def test_an_absent_section_is_refused_per_item_naming_the_version():
    """"Absent" means "not located in this version": the item is refused, not the whole file."""
    doc = {'project': 'xteink_app', 'version': '9.9.9', 'app_elf_sha256': '00' * 32,
           'patches': {'lua-apps-row': {'app_offset': '0x10', 'original': '00', 'patched': '01'}}}
    ver = stockver.parse(doc, os.path.join(stockver.DIR, '9.9.9.json'))
    assert ver.need_patch('lua-apps-row').app_offset == 0x10
    for call, item in ((ver.need_dev_stub, 'dev_stub'), (lambda: ver.need_patch('x'), "'x'"),
                       (lambda: ver.need_pack('labels'), 'labels'),
                       (ver.need_lang_count, 'lang_count')):
        with pytest.raises(stockver.StockVerError) as e:
            call()
        assert '9.9.9' in str(e.value) and item in str(e.value), str(e.value)
        assert '7.2.4.json' not in str(e.value), 'another version is never the fallback'


# ---------------------------------------------------------------- the version files themselves
def test_every_shipped_version_file_validates():
    """`tools/stockver.py versions` is the self-check: every file parses, and what it parses to is
    consistent (byte strings of equal length, packs headed or fully headerless, sane hashes)."""
    files = sorted(f for f in os.listdir(stockver.DIR) if f.endswith('.json'))
    assert '7.2.4.json' in files, files
    assert stockver.problems(force=True) == {}, 'a shipped version file does not load'
    known = stockver.versions()
    assert len(known) == len(files), 'every file must produce exactly one version'

    for v in known:
        assert os.path.splitext(os.path.basename(v.path))[0] == v.version, v.path
        assert len(v.app_elf_sha256) == 64 and int(v.app_elf_sha256, 16) >= 0
        assert stockver.get(v.version, v.project) is v
        for p in v.patches.values():
            assert len(p.original) == len(p.patched) and p.original != p.patched, p.name
            assert p.description and p.detail, f'{v.label} {p.name} has no description/detail'
        for s in v.packs.values():
            assert s.groups > 0 and s.limit_va
            assert s.header_va or (s.offsets_va and s.blob_va and s.blob_size), s.name
        if v.dev_stub:
            st = v.dev_stub
            assert st.stub[st.movi_index] == st.unpatched != st.patched
            assert st.variant(st.patched)[st.movi_index] == st.patched

    out = run('versions')
    for v in known:
        assert v.label in out and os.path.basename(v.path) in out, out


def test_a_malformed_version_file_is_reported_and_does_not_hide_the_good_ones(tmp_path, monkeypatch):
    """A half-written or wrong file lands in `problems()` with a reason; the other versions keep
    working, and an image whose version that file was supposed to describe says so."""
    monkeypatch.setattr(stockver, 'DIR', str(tmp_path))
    shutil.copyfile(os.path.join(ROOT, 'tools', 'stockver.d', '7.2.4.json'),
                    tmp_path / '7.2.4.json')
    (tmp_path / '8.0.0.json').write_text('{"project": "xteink_app", "version": "8.0.0"')  # truncated
    (tmp_path / '8.1.0.json').write_text(json.dumps({
        'project': 'xteink_app', 'version': '8.1.0', 'app_elf_sha256': '00' * 32,
        'patches': {'lua-apps-row': {'app_offset': '0x10', 'original': '00 01', 'patched': '02'}}}))

    bad = stockver.problems(force=True)
    assert len(bad) == 2, bad
    assert 'not valid JSON' in bad[str(tmp_path / '8.0.0.json')]
    assert 'must match' in bad[str(tmp_path / '8.1.0.json')], bad
    assert [v.version for v in stockver.versions()] == ['7.2.4'], 'the good file still loads'

    with pytest.raises(stockver.StockVerError) as e:
        stockver.get('8.0.0')
    assert '8.0.0.json does not load' in str(e.value) and 'not valid JSON' in str(e.value)


def test_a_version_file_that_appears_while_we_run_is_picked_up(tmp_path, monkeypatch):
    """Another agent writes tools/stockver.d/<next>.json in the middle of a session: the directory
    is re-read when its listing changes, so nothing has to be restarted."""
    monkeypatch.setattr(stockver, 'DIR', str(tmp_path))
    shutil.copyfile(os.path.join(ROOT, 'tools', 'stockver.d', '7.2.4.json'),
                    tmp_path / '7.2.4.json')
    assert [v.version for v in stockver.versions(force=True)] == ['7.2.4']

    doc = json.loads(open(os.path.join(ROOT, 'tools', 'stockver.d', '7.2.4.json')).read())
    doc.update(version='7.5.4', app_elf_sha256='05' * 32, app_sha256='06' * 32)
    (tmp_path / '7.5.4.json').write_text(json.dumps(doc))
    assert [v.version for v in stockver.versions()] == ['7.2.4', '7.5.4'], 'no force, no restart'
    assert stockver.known_line() == 'xteink_app 7.2.4, xteink_app 7.5.4'


def test_the_ota_image_is_handled_or_refused_but_never_guessed():
    """The device took an OTA to 7.5.4. Either a version file for it has appeared -- then it is
    identified and its own numbers are used -- or it is refused with the unknown-version message.
    What must never happen is 7.2.4's offsets being used on it."""
    ota = os.path.join(ROOT, 'images', 'device', 'stock-app1-7.5.4.bin')
    if not os.path.exists(ota):
        pytest.skip('images/device/stock-app1-7.5.4.bin not available')
    data = bytearray(open(ota, 'rb').read())
    assert stockver.app_desc(data)['version'] == '7.5.4'
    try:
        base, ver = stockver.identify(data)
    except stockver.UnknownVersion as e:
        assert '7.5.4' in str(e) and 'tools/stockver.d/' in str(e)
        return
    assert ver.version == '7.5.4' and os.path.basename(ver.path) == '7.5.4.json'
    assert ver.app_elf_sha256 == stockver.app_desc(data, base)['elf_sha256']
    for name, p in ver.patches.items():                  # its own sites, not 7.2.4's
        assert p.state(data, base) in ('original', 'applied'), \
            f'7.5.4 {name} at {p.app_offset:#x} holds neither of its byte strings'
