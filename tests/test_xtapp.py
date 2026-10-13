"""tools/xtapp.py: the `.xtapp` container, the manifest rules and the card install -- no emulator.

A Lua app is a card directory `/sdcard/XTApps/<app_id>/` with `manifest.json`, `index.lua` and an
`app.xtapp` container; the stock's app list (`FUN_421c37e8`) silently **drops** any directory whose
container fails to open, so getting those 0x80 header bytes right is the whole difference between an
app that appears and one that does not. The reference is the container the first successful run used
(session 7): `tests/data/hello-app/manifest.json` is that manifest, and `pack` has to reproduce its
313 bytes exactly -- header field by header field, and the manifest as compact JSON.

Also here: the permission rule (`FUN_4206645c` accepts only `sys.battery` and `sys.status` and
rejects the whole app on anything else -- the mistake that made the first attempt vanish), `info` as
the inverse of `pack`, the `new` scaffold, and `install` onto a real FAT32 card image built by
`tools/mksd.py` (skipped when mtools/dosfstools are missing). Everything runs in tmp_path; a few
seconds, no QEMU.

The Licorice tests at the end cover the key-wrapped (XTOS 261013) format: the ECDH/HKDF/AES-KW DEK
wrap (self-consistent: what `licorice_wrap_dek` produces, `licorice_unwrap_dek` -- the device side --
recovers), the container header the installer parses, the stricter manifest rules and the classic/
licorice `info` split. They need the `cryptography` package and skip without it; the live install is
tests/test_licorice_apps.py (emulator) and the format is docs/licorice-apps.md.
"""
import collections, json, os, shutil, struct, subprocess, sys
import pytest
from conftest import ROOT, PY

import xtapp                                                                   # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'xtapp.py')
HELLO = os.path.join(ROOT, 'tests', 'data', 'hello-app')

# The container the stock accepted in session 7, spelled out rather than recomputed.
REFERENCE_MANIFEST = (
    '{"app_id":"hello","display_name":"Hello Lua","version":"1.0","author":"lc",'
    '"entry":"index.lua","permissions":["sys.battery","sys.status"],'
    '"lockscreen":{"enabled":true,"interval_sec":5}}')
REFERENCE_HEADER = bytes.fromhex(
    '58 54 41 50 01 00 80 00 00 00 00 00 00 00 00 00'      # "XTAP", version 1, header size 0x80
    '80 00 00 00 b9 00 00 00 00 00 00 00 00 00 00 00'      # manifest at 0x80, 185 bytes
    .replace(' ', '')) + bytes(0x60)                       # 0x20..0x7f: zero (0x34 = 0: unencrypted)
REFERENCE = REFERENCE_HEADER + REFERENCE_MANIFEST.encode()


def run(*args, expect=0):
    r = subprocess.run([PY, TOOL, *map(str, args)], capture_output=True, text=True)
    assert r.returncode == expect, \
        f'xtapp {args} exited {r.returncode}, wanted {expect}:\n{r.stdout}\n{r.stderr}'
    return r.stdout + r.stderr


@pytest.fixture
def app_dir(tmp_path):
    """A writable copy of tests/data/hello-app (pack writes app.xtapp into it)."""
    dst = tmp_path / 'hello'
    shutil.copytree(HELLO, dst)
    return dst


def write_manifest(d, obj):
    d.mkdir(exist_ok=True)
    (d / 'manifest.json').write_text(json.dumps(obj, indent=2))
    (d / 'index.lua').write_text('function on_draw() end\n')
    return d


def test_pack_reproduces_the_reference_container(app_dir):
    """313 bytes: the 0x80 header the firmware parses plus the manifest as compact JSON. This is the
    exact file the stock listed as "Hello Lua"; a change here means the app may stop appearing."""
    assert len(REFERENCE) == 313 and len(REFERENCE_MANIFEST) == 0xb9
    out = run('pack', app_dir)
    got = (app_dir / 'app.xtapp').read_bytes()
    assert got == REFERENCE, 'pack no longer produces the container the stock accepted'
    assert got[:4] == b'XTAP' and got[0x80:] == REFERENCE_MANIFEST.encode()
    assert got[0x34:0x38] == b'\0\0\0\0', 'a non-zero word at 0x34 marks an encrypted payload'
    assert '313 bytes = 0x80 header + 185 manifest' in out, out
    assert "app_id 'hello'" in out and "'Hello Lua'" in out, out

    run('pack', app_dir, '-o', str(app_dir.parent / 'elsewhere.xtapp'))     # -o leaves the dir alone
    assert (app_dir.parent / 'elsewhere.xtapp').read_bytes() == REFERENCE
    assert (app_dir / 'app.xtapp').read_bytes() == REFERENCE


def test_info_round_trips_the_container(app_dir):
    """`info` reads back every header field `pack` wrote, and the manifest it carried."""
    run('pack', app_dir)
    data = (app_dir / 'app.xtapp').read_bytes()
    head, manifest = xtapp.read_container(data)
    assert head == {'magic': 'XTAP', 'version': 1, 'header_size': 0x80, 'flags': 0,
                    'manifest_offset': 0x80, 'manifest_length': 0xb9, 'word_0x30': 0,
                    'word_0x34': 0, 'file_bytes': 313}
    assert manifest == json.loads((app_dir / 'manifest.json').read_text())
    assert xtapp.build_container(manifest) == data, 'read_container/build_container must round-trip'

    out = run('info', app_dir / 'app.xtapp')
    for want in ('XTAP', '0x80 (128)', '0xb9 (185)', '"display_name": "Hello Lua"'):
        assert want in out, out
    doc = json.loads(run('info', app_dir / 'app.xtapp', '--json'))
    assert doc['header'] == head and doc['manifest'] == manifest


@pytest.mark.parametrize('broken, message', [
    ({'app_id': 'x', 'display_name': 'X', 'permissions': ['lockscreen']}, 'lockscreen'),
    ({'app_id': 'x', 'display_name': 'X', 'permissions': ['sys.battery', 'fs.write']}, 'fs.write'),
    ({'app_id': 'x'}, 'display_name'),
    ({'display_name': 'X'}, 'app_id'),
    ({'app_id': '', 'display_name': 'X'}, 'non-empty'),
    ({'app_id': 'a/b', 'display_name': 'X'}, 'no slashes'),
    ({'app_id': 'x', 'display_name': 'X', 'permissions': 'sys.battery'}, 'must be a list'),
])
def test_a_manifest_the_firmware_would_reject_is_refused(tmp_path, broken, message):
    """The stock says nothing when it drops an app, so the tool has to say it instead: every
    rejection names the offending value."""
    d = write_manifest(tmp_path / 'bad', broken)
    out = run('pack', d, expect=2)
    assert message in out, out
    assert not (d / 'app.xtapp').exists(), 'a refused manifest must not leave a container behind'
    with pytest.raises(xtapp.XtappError, match=message.replace('/', '.')):
        xtapp.validate_manifest(broken)


def test_a_container_that_is_not_the_xtap_form_is_refused(tmp_path, app_dir):
    """`info` checks what FUN_420664d8 checks: magic, version, header size, manifest window, and
    the word at 0x34 that marks an encrypted payload."""
    run('pack', app_dir)
    good = (app_dir / 'app.xtapp').read_bytes()
    for name, blob, message in (
            ('magic', b'XTAQ' + good[4:], 'magic'),
            ('version', good[:4] + b'\x02\x00' + good[6:], 'version 2'),
            ('offset', good[:0x10] + (0x90).to_bytes(4, 'little') + good[0x14:], 'manifest offset'),
            ('length', good[:0x14] + (0x1000).to_bytes(4, 'little') + good[0x18:], 'runs past'),
            ('huge', good[:0x14] + (0x9999).to_bytes(4, 'little') + good[0x18:], 'expected 1..8192'),
            ('encrypted', good[:0x34] + b'\x01\x00\x00\x00' + good[0x38:], '0x34'),
            ('short', good[:0x40], 'shorter than')):
        f = tmp_path / f'{name}.xtapp'
        f.write_bytes(blob)
        assert message in run('info', f, expect=2), name


def test_new_scaffolds_an_app_that_packs_and_documents_the_api(tmp_path):
    """`new` has to produce something that already loads: a valid manifest, a container, and an
    index.lua that says what the host actually offers."""
    out = run('new', 'clock', tmp_path / 'clock')
    d = tmp_path / 'clock'
    assert (d / 'manifest.json').exists() and (d / 'index.lua').exists() and (d / 'app.xtapp').exists()
    manifest = json.loads((d / 'manifest.json').read_text())
    assert manifest['app_id'] == 'clock' and manifest['display_name'] == 'Clock'
    assert manifest['permissions'] == list(xtapp.PERMISSIONS)
    xtapp.validate_manifest(manifest)
    head, packed = xtapp.read_container((d / 'app.xtapp').read_bytes())
    assert packed == manifest and head['manifest_offset'] == 0x80
    assert 'Clock' in out and 'index.lua' in out

    lua = (d / 'index.lua').read_text()
    for verb in ('on_load', 'on_enter', 'on_draw', 'on_tick', 'on_input',
                 'g:rect', 'g:text', 'g:circle', 'g:line', 'g:clear',
                 'ctx.invalidate', 'ctx.set_tick_rate', 'ctx.log.info', 'ctx.display.full_refresh'):
        assert verb in lua, f'the template does not document {verb}'
    assert 'no `print`' in lua or 'NO `print`' in lua, 'the template must warn that print is dead'
    assert '480' in lua and '800' in lua, 'the template must give the frame size'

    run('pack', d)                                       # packing the scaffold again is a no-op
    assert xtapp.read_container((d / 'app.xtapp').read_bytes())[1] == manifest
    assert 'exists' in run('new', 'clock', d, expect=2)   # never overwrite an app


def test_install_puts_the_app_on_a_card_image(tmp_path, app_dir):
    """The card side: /XTApps/<app_id>/ with a freshly packed container and every file of the
    directory, on a real FAT32 image (mtools, partition at 1 MiB)."""
    for tool in ('mkfs.vfat', 'mcopy', 'mdir'):
        if not shutil.which(tool):
            pytest.skip(f'{tool} not available')
    img = tmp_path / 'sd.img'
    subprocess.run([PY, os.path.join(ROOT, 'tools', 'mksd.py'), str(img), '--size', '64M'],
                   check=True, stdout=subprocess.DEVNULL)
    assert not (app_dir / 'app.xtapp').exists(), 'the data dir holds sources only'

    out = run('install', img, app_dir)
    assert '/XTApps/hello' in out and 'app.xtapp' in out, out
    listing = subprocess.run(['mdir', '-i', f'{img}@@{xtapp.PART_OFFSET}', '-b', '::/XTApps/hello'],
                             capture_output=True, text=True, check=True).stdout
    for name in ('app.xtapp', 'index.lua', 'manifest.json'):
        assert name in listing.lower(), listing
    back = tmp_path / 'back'
    back.mkdir()
    subprocess.run(['mcopy', '-i', f'{img}@@{xtapp.PART_OFFSET}', '-o',
                    '::/XTApps/hello/app.xtapp', '::/XTApps/hello/index.lua', str(back)], check=True)
    assert (back / 'app.xtapp').read_bytes() == REFERENCE
    assert (back / 'index.lua').read_bytes() == (app_dir / 'index.lua').read_bytes()
    assert not (app_dir / 'app.xtapp').exists(), 'install must not write into the source directory'

    run('install', img, app_dir, '--as', 'hello2')       # a second copy under another name
    assert 'hello2' in subprocess.run(['mdir', '-i', f'{img}@@{xtapp.PART_OFFSET}', '-b', '::/XTApps'],
                                      capture_output=True, text=True).stdout
    assert 'not a directory' in run('install', img, tmp_path / 'nope', expect=2)


# ---------------------------------------------------------------- Licorice (XTOS 261013) key wrap
def _keypair():
    """A throwaway P-256 keypair (uncompressed public bytes, private scalar int): proves the wrap
    ALGORITHM without the firmware's own key, which only the device/emulator can confirm."""
    pytest.importorskip('cryptography')
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return pub, priv.private_numbers().private_value


def test_licorice_wrap_round_trips():
    """`licorice_wrap_dek` (ours) and `licorice_unwrap_dek` (the device side, FUN_4210a908) are
    inverse: a DEK wrapped for Q and unwrapped with d comes back byte for byte, with the sizes the
    firmware reads -- a 65-byte ephemeral point (0x04-led) and a 24-byte AES-KW pack."""
    pub, scalar = _keypair()
    dek, eph, wrapped = xtapp.licorice_wrap_dek(pub)
    assert len(dek) == 16 and len(eph) == 65 and eph[0] == 0x04 and len(wrapped) == 24
    assert xtapp.licorice_unwrap_dek(scalar, eph, wrapped) == dek
    # a fresh wrap uses a new ephemeral key and a new pack each time, but still unwraps to the DEK
    dek2, eph2, wrapped2 = xtapp.licorice_wrap_dek(pub, dek)
    assert eph2 != eph and wrapped2 != wrapped and dek2 == dek
    assert xtapp.licorice_unwrap_dek(scalar, eph2, wrapped2) == dek


def test_licorice_kek_and_section_hmac_match_the_firmware_construction():
    """The KEK is HKDF-SHA256 built from two HMACs (extract with the wrap salt, expand with the
    appdek info + a 0x01 block); a section tag is HMAC-SHA256(DEK, bytes). Spelled out here so a
    change to either construction is caught without the device."""
    import hashlib, hmac
    pytest.importorskip('cryptography')
    shared = bytes(range(32))
    prk = hmac.new(xtapp.LICORICE_WRAP_SALT, shared, hashlib.sha256).digest()
    want = hmac.new(prk, xtapp.LICORICE_WRAP_INFO + b'\x01', hashlib.sha256).digest()[:16]
    assert xtapp.licorice_kek(shared) == want and len(want) == 16
    assert xtapp.LICORICE_WRAP_SALT == b'xteink.xtapp.wrap.36036010c7bd05af'
    assert xtapp.licorice_section_hmac(b'\0' * 16, b'code') == \
        hmac.new(b'\0' * 16, b'code', hashlib.sha256).digest()


def test_build_licorice_container_lays_out_the_header_the_installer_parses():
    """The 0x80 header confirmed by a live install (tests/test_licorice_apps.py): XTAP magic,
    version u16 = 1, header-size u16 = 0x80, total-size u32 at 0x08, feature mask u32 at 0x0c,
    manifest offset/length at 0x10/0x14; the manifest carries the dek_wrap_* fields and the DEK in it
    unwraps with the matching scalar."""
    pub, scalar = _keypair()
    manifest = collections.OrderedDict((('app_id', 'probe'), ('display_name', 'API Probe'),
                                        ('version', '1.0'), ('permissions', ['sys.battery'])))
    data, dek = xtapp.build_licorice_container(manifest, pub)

    assert data[0:4] == b'XTAP'
    assert struct.unpack_from('<HH', data, 4) == (1, 0x80)
    assert struct.unpack_from('<I', data, 0x08)[0] == len(data)          # total size
    assert struct.unpack_from('<I', data, 0x0c)[0] == 0                  # feature mask: no sections
    assert struct.unpack_from('<II', data, 0x10) == (0x80, len(data) - 0x80)

    head, obj = xtapp.read_licorice_container(data)
    assert head == {'magic': 'XTAP', 'version': 1, 'header_size': 0x80, 'total_size': len(data),
                    'feature_mask': 0, 'manifest_offset': 0x80, 'manifest_length': len(data) - 0x80,
                    'file_bytes': len(data), 'has_dek_wrap': True}
    assert obj['app_id'] == 'probe' and obj['display_name'] == 'API Probe'
    assert obj['dek_wrap_alg'] == xtapp.LICORICE_ALG and obj['dek_wrap_key_id'] == xtapp.LICORICE_KEY_ID
    eph = bytes.fromhex(obj['dek_wrap_ephemeral_pub'])
    wrapped = bytes.fromhex(obj['wrapped_dek_pack'])
    assert len(eph) == 65 and eph[0] == 0x04 and len(wrapped) == 24
    assert xtapp.licorice_unwrap_dek(scalar, eph, wrapped) == dek, 'the DEK in the container must unwrap'


def test_info_tells_licorice_from_classic():
    """A classic container has no dek_wrap_* fields; a Licorice one does. `is_licorice_container`
    and the `info` command route each to the right reader."""
    pub, _ = _keypair()
    classic = xtapp.build_container(collections.OrderedDict((('app_id', 'hello'),
                                                             ('display_name', 'Hello'))))
    lic, _ = xtapp.build_licorice_container(collections.OrderedDict((('app_id', 'probe'),
                                                                     ('display_name', 'API Probe'))), pub)
    assert xtapp.is_licorice_container(lic) is True
    assert xtapp.is_licorice_container(classic) is False

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cf, lf = os.path.join(d, 'classic.xtapp'), os.path.join(d, 'lic.xtapp')
        open(cf, 'wb').write(classic); open(lf, 'wb').write(lic)
        assert 'classic' in run('info', cf) and 'word_0x34' in run('info', cf)
        out = run('info', lf)
        assert 'licorice, key-wrapped' in out and 'total_size' in out and 'feature_mask' in out
        assert json.loads(run('info', lf, '--json'))['format'] == 'licorice'


@pytest.mark.parametrize('broken, message', [
    ({'app_id': 'gull-rock', 'display_name': 'X'}, 'letter'),         # hyphen
    ({'app_id': 'probe2', 'display_name': 'X'}, 'letter'),            # digit
    ({'app_id': 'probe', 'display_name': 'X', 'version': '2.0'}, 'version'),
    ({'app_id': 'probe', 'display_name': 'X', 'permissions': ['fs.write']}, 'not one Licorice knows'),
    ({'app_id': 'probe', 'display_name': 'X', 'dek_wrap_alg': 'x'}, 'dek_wrap'),
    ({'display_name': 'X'}, 'app_id'),
])
def test_licorice_manifest_rules(broken, message):
    """Licorice is stricter than classic: an ASCII-letter app_id, a known version, its own permission
    set, and no caller-supplied dek_wrap_* (pack writes those)."""
    pytest.importorskip('cryptography')
    with pytest.raises(xtapp.XtappError, match=message):
        xtapp.validate_manifest_licorice(broken)


def test_licorice_accepts_the_wider_permissions_and_versions():
    pytest.importorskip('cryptography')
    for perms in ([], ['sys.battery', 'sys.status'], ['net.http', 'net.https'], ['storage.app']):
        obj = {'app_id': 'netapp', 'display_name': 'X', 'version': '0.8', 'permissions': perms}
        assert xtapp.validate_manifest_licorice(dict(obj)) is not None
    for ver in xtapp.LICORICE_VERSIONS:
        xtapp.validate_manifest_licorice({'app_id': 'probe', 'display_name': 'X', 'version': ver})


@pytest.mark.skipif(not os.path.exists(xtapp.LICORICE_IMAGE), reason='Licorice app image not available')
def test_licorice_device_pubkey_is_on_curve():
    """The device key Q = d*G read from the firmware image is a valid P-256 point (65-byte 0x04
    form). The scalar's big-endian reading is the one a live install confirmed."""
    pytest.importorskip('cryptography')
    from cryptography.hazmat.primitives.asymmetric import ec
    pub = xtapp.licorice_device_pubkey()
    assert len(pub) == 65 and pub[0] == 0x04
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)   # raises if off-curve
