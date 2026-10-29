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
licorice `info` split; and the code section the launch decrypts: the "XTCO" table, AES-128-CTR under
the DEK checked against a block-by-block re-implementation of the firmware's FUN_4210ad78 (random
access at any offset included), hmac_code over the ciphertext, the section table and iv_code /
hmac_code fields. They need the `cryptography` package and skip without it; the live install and
launch are tests/test_licorice_apps.py (emulator) and the format is docs/licorice-apps.md.
"""
import collections, hashlib, json, os, shutil, struct, subprocess, sys
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
    assert hashlib.sha256(pub).hexdigest()[:16] == xtapp.LICORICE_KEY_ID   # the id is sha256(Q)[:8]


def test_licorice_device_pubkey_refuses_a_scalar_that_is_not_the_key(tmp_path):
    """A build keeps something else at its table address (26102901 moved the scalar): a valid-looking
    scalar whose Q does not hash to the key id is refused, not used to wrap a package the device
    cannot open; a build the table does not know is refused by name."""
    pytest.importorskip('cryptography')
    def image(tag):
        seg = bytes(range(1, 33))
        return (bytes([0xE9, 1]) + bytes(22) + struct.pack('<II', xtapp.LICORICE_SCALAR_VA['v261013'], 32)
                + seg + b'\0XTOS Licorice ' + tag + b'\0')
    (tmp_path / 'moved.bin').write_bytes(image(b'261013'))
    with pytest.raises(xtapp.XtappError, match='is not the key 36036010c7bd05af'):
        xtapp.licorice_device_pubkey(str(tmp_path / 'moved.bin'))
    (tmp_path / 'new.bin').write_bytes(image(b'26123101'))
    with pytest.raises(xtapp.XtappError, match="'v26123101' has no known key address"):
        xtapp.licorice_device_pubkey(str(tmp_path / 'new.bin'))


@pytest.mark.parametrize('app', ['stock-app1-v261013.bin', 'stock-app0-v26102901.bin'])
def test_licorice_builds_share_one_device_key(app):
    """Every known build's table address holds the same key: the one the id names."""
    path = os.path.join(ROOT, 'images', 'device', app)
    if not os.path.exists(path):
        pytest.skip(f'{path} not available')
    pytest.importorskip('cryptography')
    assert hashlib.sha256(xtapp.licorice_device_pubkey(path)).hexdigest()[:16] == xtapp.LICORICE_KEY_ID


# ---------------------------------------------------------------- Licorice code section (launch)
def _firmware_ctr(key, iv, data, offset=0):
    """FUN_4210ad78 re-implemented from its decompile, block by block on AES-ECB: copy the IV into the
    counter, add offset >> 4 big-endian over all 16 bytes (FUN_4224d258), and with offset & 15 != 0
    encrypt the counter once into the stream block and step it (the mbedtls_aes_crypt_ecb + +1 the
    firmware does before calling mbedtls_aes_crypt_ctr); then mbedtls_aes_crypt_ctr itself: a new
    stream block whenever nc_off wraps to 0, the counter incremented with carry from byte 15 up.
    An oracle independent of `xtapp.licorice_ctr` (which uses the library's CTR mode)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    ecb = Cipher(algorithms.AES(bytes(key)), modes.ECB()).encryptor()
    ctr = bytearray(iv)
    add = offset >> 4
    for i in range(15, -1, -1):                       # FUN_4224d258: a 64-bit value into 16 bytes
        add += ctr[i]
        ctr[i] = add & 0xff
        add >>= 8

    def step():
        for i in range(15, -1, -1):
            ctr[i] = (ctr[i] + 1) & 0xff
            if ctr[i]:
                break
    nc_off, stream = offset & 15, b''
    if nc_off:
        stream = ecb.update(bytes(ctr))
        step()
    out = bytearray()
    for b in bytes(data):
        if nc_off == 0:
            stream = ecb.update(bytes(ctr))
            step()
        out.append(b ^ stream[nc_off])
        nc_off = (nc_off + 1) & 15
    return bytes(out)


def test_licorice_ctr_is_the_firmware_decrypt():
    """`licorice_ctr` against the firmware's AES-128-CTR (FUN_4210ad78) re-implemented above: the same
    keystream from offset 0, the same bytes for any slice decrypted on its own at its own offset (how
    FUN_42109d7c reads one file out of a streamed section), the counter carrying over all 128 bits,
    and encryption = decryption."""
    pytest.importorskip('cryptography')
    import random
    rnd = random.Random(261013)
    key = bytes(rnd.randrange(256) for _ in range(16))
    data = bytes(rnd.randrange(256) for _ in range(1000))
    for iv in (bytes(rnd.randrange(256) for _ in range(16)), b'\xff' * 16, bytes(8) + b'\xff' * 8):
        whole = xtapp.licorice_ctr(key, iv, data)
        assert whole == _firmware_ctr(key, iv, data) and whole != data
        assert xtapp.licorice_ctr(key, iv, whole) == data
        for off, n in ((0, 1), (5, 30), (16, 16), (17, 200), (999, 1), (333, 667)):
            part = xtapp.licorice_ctr(key, iv, data[off:off + n], off)
            assert part == whole[off:off + n] == _firmware_ctr(key, iv, data[off:off + n], off), (iv, off)


def test_xtco_lays_out_the_code_section_the_firmware_parses():
    """The decrypted code section (FUN_42108c44): "XTCO", u16 version 1, u16 0, u32 count, u32 table
    end = 16 + 104 * count, then name[96] / u32 offset from the table end / u32 length per file."""
    files = [('index.lua', b'function on_draw() end\n'), ('lib/util.lua', b'return {}\n')]
    blob = xtapp.build_xtco(files)
    assert blob[:4] == b'XTCO' and struct.unpack_from('<HHII', blob, 4) == (1, 0, 2, 16 + 2 * 104)
    end = 16 + 2 * 104
    assert blob[16:16 + 9] == b'index.lua' and blob[16 + 9:16 + 96] == bytes(87)
    assert struct.unpack_from('<II', blob, 16 + 96) == (0, len(files[0][1]))
    assert struct.unpack_from('<II', blob, 16 + 104 + 96) == (len(files[0][1]), len(files[1][1]))
    assert blob[end:] == files[0][1] + files[1][1]
    assert xtapp.read_xtco(blob) == files
    for bad, message in (([], '1..32'), ([('f%d.lua' % i, b'') for i in range(33)], '1..32'),
                         ([('', b'x')], 'no NUL'), ([('x' * 96, b'')], 'no NUL'),
                         ([('a.lua', b''), ('a.lua', b'')], 'twice')):
        with pytest.raises(xtapp.XtappError, match=message):
            xtapp.build_xtco(bad)
    with pytest.raises(xtapp.XtappError, match='not an XTCO'):
        xtapp.read_xtco(b'XTCQ' + blob[4:])
    with pytest.raises(xtapp.XtappError, match='table end'):
        xtapp.read_xtco(blob[:12] + struct.pack('<I', end + 1) + blob[16:])
    with pytest.raises(xtapp.XtappError, match='does not fit'):
        xtapp.read_xtco(blob[:-1])


def test_licorice_code_section_round_trips_through_the_firmware_decrypt():
    """A container with a code section: feature mask 0x2, the section at 0x18 (after the manifest,
    inside the total size), iv_code / hmac_code in the manifest; the stored bytes are ciphertext
    (no XTCO, no source), the tag is HMAC-SHA256(DEK, ciphertext), and the firmware's decrypt
    (`_firmware_ctr`, key = DEK) gives back the XTCO table and the files. A flipped ciphertext byte
    fails the tag (the install's "Install failed"); a wrong IV decrypts to no XTCO (the launch's
    "Cannot run yet") -- both seen in the emulator, docs/licorice-apps.md."""
    import hashlib, hmac
    pub, scalar = _keypair()
    files = [('index.lua', b'local n = 0\nfunction on_draw(ctx, g) g:clear(0) g:text(10, 40, "hi") end\n')]
    manifest = collections.OrderedDict((('app_id', 'probe'), ('display_name', 'API Probe'),
                                        ('version', '1.0'), ('entry', 'index.lua')))
    dek, iv = bytes(range(16)), bytes(range(16, 32))
    data, got_dek = xtapp.build_licorice_container(manifest, pub, dek=dek, code_files=files, iv=iv)
    assert got_dek == dek
    head, obj = xtapp.read_licorice_container(data)
    sec = xtapp.licorice_sections(data)
    off, length = sec['code']
    assert head['feature_mask'] == xtapp.LICORICE_FEATURE_CODE == sec['feature_mask'] == 0x2
    assert off >= 0x80 + head['manifest_length'] and off % 16 == 0
    assert off + length == head['total_size'] == len(data)
    assert all(sec[n] == (0, 0) for n in ('assets', 'unused', 'icons', 'data', 'fonts'))
    assert obj['iv_code'] == iv.hex() and len(obj['hmac_code']) == 64
    stored = data[off:off + length]
    assert b'XTCO' not in stored and b'on_draw' not in stored, 'the code section must be encrypted'
    assert bytes.fromhex(obj['hmac_code']) == hmac.new(dek, stored, hashlib.sha256).digest()
    plain = _firmware_ctr(dek, iv, stored)
    assert plain == xtapp.build_xtco(files) and xtapp.read_xtco(plain) == files
    assert xtapp.read_licorice_code(data, dek) == files
    # the DEK the device unwraps is the one the section is encrypted with
    eph, wrapped = bytes.fromhex(obj['dek_wrap_ephemeral_pub']), bytes.fromhex(obj['wrapped_dek_pack'])
    assert xtapp.licorice_unwrap_dek(scalar, eph, wrapped) == dek

    tampered = bytearray(data)
    tampered[off + 20] ^= 0x55
    with pytest.raises(xtapp.XtappError, match='hmac_code'):
        xtapp.read_licorice_code(bytes(tampered), dek)
    bad_iv = bytearray(iv)
    bad_iv[0] ^= 1
    swapped = data.replace(iv.hex().encode(), bytes(bad_iv).hex().encode())
    assert len(swapped) == len(data)
    with pytest.raises(xtapp.XtappError, match='not an XTCO'):
        xtapp.read_licorice_code(swapped, dek)
    # a fresh IV each time unless one is given
    a, _ = xtapp.build_licorice_container(manifest, pub, dek=dek, code_files=files)
    b, _ = xtapp.build_licorice_container(manifest, pub, dek=dek, code_files=files)
    assert xtapp.read_licorice_container(a)[1]['iv_code'] != xtapp.read_licorice_container(b)[1]['iv_code']


def test_licorice_code_files_takes_every_lua_file_entry_first(tmp_path):
    """What goes into the code section: every *.lua, relative paths with "/" (so `require "lib.x"`
    finds "lib/x.lua"), the entry first; other files are reported, not packed; no entry is an error
    (the Lua host's "entry missing")."""
    d = write_manifest(tmp_path / 'app', {'app_id': 'demo', 'display_name': 'Demo'})
    (d / 'lib').mkdir()
    (d / 'lib' / 'x.lua').write_text('return 1\n')
    (d / 'a.lua').write_text('return 2\n')
    (d / 'story.txt').write_text('text\n')
    files, skipped = xtapp.licorice_code_files(str(d))
    assert [n for n, _ in files] == ['index.lua', 'a.lua', 'lib/x.lua']
    assert skipped == ['story.txt']
    (d / 'index.lua').unlink()
    with pytest.raises(xtapp.XtappError, match='entry missing'):
        xtapp.licorice_code_files(str(d))
    assert [n for n, _ in xtapp.licorice_code_files(str(d), 'a.lua')[0]] == ['a.lua', 'lib/x.lua']


@pytest.mark.parametrize('broken, message', [
    ({'app_id': 'probe', 'display_name': 'X', 'iv_code': '00' * 16}, 'iv_code'),
    ({'app_id': 'probe', 'display_name': 'X', 'hmac_code': '00' * 32}, 'hmac_code'),
    ({'app_id': 'probe', 'display_name': 'X', 'entry': 'x' * 32 + '.lua'}, '1..31 bytes'),
])
def test_licorice_manifest_rules_for_the_code_section(broken, message):
    """pack writes iv_code / hmac_code itself; the entry name must fit FUN_42108648's 32-byte field."""
    pytest.importorskip('cryptography')
    with pytest.raises(xtapp.XtappError, match=message):
        xtapp.validate_manifest_licorice(broken)


@pytest.mark.skipif(not os.path.exists(xtapp.LICORICE_IMAGE), reason='Licorice app image not available')
def test_pack_licorice_cli_builds_a_runnable_package(tmp_path):
    """`pack --format licorice` packs apps/probe's index.lua into an encrypted code section (the
    package tests/test_licorice_apps.py installs and runs); `info` shows the section; with
    `--manifest-only` there is none (feature mask 0)."""
    pytest.importorskip('cryptography')
    out = tmp_path / 'probe.xtapp'
    text = run('pack', os.path.join(ROOT, 'apps', 'probe'), '--format', 'licorice', '-o', out)
    assert 'code section: index.lua' in text and 'feature mask 0x2' in text, text
    data = out.read_bytes()
    off, length = xtapp.licorice_sections(data)['code']
    assert length > 0 and off + length == len(data)
    info = run('info', out)
    assert f'section code     {off:#x} + {length} (encrypted)' in info, info
    assert 'warning' not in info, info
    assert json.loads(run('info', out, '--json'))['sections']['code'] == [off, length]
    bare = tmp_path / 'bare.xtapp'
    run('pack', os.path.join(ROOT, 'apps', 'probe'), '--format', 'licorice', '--manifest-only', '-o', bare)
    assert xtapp.licorice_sections(bare.read_bytes())['code'] == (0, 0)
    assert xtapp.licorice_sections(bare.read_bytes())['feature_mask'] == 0
